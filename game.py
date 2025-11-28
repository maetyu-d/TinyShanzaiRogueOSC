# game.py
import random
import socket
import struct
from contextlib import suppress
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

WALL = "#"
FLOOR = "."
STAIRS_DOWN = ">"
MAX_HP = 10


@dataclass
class Entity:
    x: int
    y: int
    char: str
    name: str
    hp: int = 3


@dataclass
class Item:
    x: int
    y: int
    char: str
    name: str
    kind: str  # "weapon" or "potion"
    power: int = 0  # damage bonus or heal amount


@dataclass
class Game:
    width: int = 40
    height: int = 20
    num_monsters: int = 8
    num_items: int = 6

    tiles: List[List[str]] = field(init=False)
    player: Optional[Entity] = field(init=False, default=None)
    monsters: List[Entity] = field(init=False, default_factory=list)
    items: List[Item] = field(init=False, default_factory=list)
    messages: List[str] = field(default_factory=list)

    level: int = 1
    inventory: List[Item] = field(default_factory=list)
    current_weapon: Optional[Item] = None

    _monster_turn_counter: int = 0

    # Nezha meta
    nezha_phase: int = 0  # 0–2; up to 3 phases

    # OSC
    osc_host: str = "127.0.0.1"
    osc_port: int = 9001
    _osc_sock: socket.socket = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._osc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.new_level(first=True)
        self._send_osc_state(event="game_start")

    # ---------------- LEVEL GENERATION ----------------
    def new_level(self, first: bool = False) -> None:
        if not first:
            self.level += 1
            self.messages.append(f"You descend to cavern level {self.level}.")

        self.tiles = [[WALL for _ in range(self.width)] for _ in range(self.height)]

        x, y = self.width // 2, self.height // 2
        for _ in range(self.width * self.height * 4):
            self.tiles[y][x] = FLOOR
            dx, dy = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            x = max(1, min(self.width - 2, x + dx))
            y = max(1, min(self.height - 2, y + dy))

        px, py = self.find_random_floor()
        if first or self.player is None:
            self.player = Entity(px, py, "@", "Player", hp=MAX_HP)
        else:
            self.player.x = px
            self.player.y = py

        self.monsters = []
        # base monsters
        for _ in range(self.num_monsters):
            mx, my = self.find_random_floor()
            hp = 3 + max(0, self.level - 1)
            self.monsters.append(Entity(mx, my, "g", "Goblin", hp=hp))

        # Nezha spawn: from level 2 onward, up to 3 phases total
        if self.level >= 2 and self.nezha_phase < 3:
            nx, ny = self.find_random_floor()
            hp = 8 + self.nezha_phase * 5
            self.monsters.append(Entity(nx, ny, "N", "Nezha", hp=hp))
            self.messages.append(f"NEZHA PROTOCOL phase {self.nezha_phase+1} detected in this cavern.")
            self._osc_send("/event", "nezha_spawn")

        self.items = []
        for _ in range(self.num_items):
            ix, iy = self.find_random_floor()
            kind = random.choice(["weapon", "weapon", "potion"])
            if kind == "weapon":
                weapon_defs = [
                    ("/", "Rusty Dagger", 1),
                    ("/", "Short Sword", 2),
                    (")", "War Axe", 3),
                    (")", "Crystal Blade", 4),
                ]
                char, name, power = random.choice(weapon_defs)
                power += max(0, self.level - 1) // 2
                self.items.append(Item(ix, iy, char, name, "weapon", power))
            else:
                power = 4 + self.level
                self.items.append(Item(ix, iy, "!", "Healing Potion", "potion", power))

        sx, sy = self.find_random_floor()
        self.tiles[sy][sx] = STAIRS_DOWN

        if first:
            self.messages = ["Welcome to Tiny Shanzai Rogue."]
        else:
            self.messages.append("Concrete corridors shift below...")

        self._send_osc_state(event="new_level")

    def find_random_floor(self) -> tuple[int, int]:
        while True:
            x = random.randint(1, self.width - 2)
            y = random.randint(1, self.height - 2)
            if (
                self.tiles[y][x] == FLOOR
                and self.get_entity_at(x, y) is None
                and self.get_item_at(x, y) is None
            ):
                return x, y

    # ---------------- HELPERS ----------------
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get_entity_at(self, x: int, y: int) -> Optional[Entity]:
        if getattr(self, "player", None) is not None:
            if self.player.x == x and self.player.y == y:
                return self.player
        for m in self.monsters:
            if m.x == x and m.y == y and m.hp > 0:
                return m
        return None

    def get_item_at(self, x: int, y: int) -> Optional[Item]:
        for it in self.items:
            if it.x == x and it.y == y:
                return it
        return None

    def is_walkable(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        if self.tiles[y][x] not in (FLOOR, STAIRS_DOWN):
            return False
        if self.get_entity_at(x, y) is not None:
            return False
        return True

    # ---------------- PLAYER ACTIONS ----------------
    def move_player(self, dx: int, dy: int) -> None:
        if self.player is None:
            return

        new_x = self.player.x + dx
        new_y = self.player.y + dy

        if not self.in_bounds(new_x, new_y):
            self.messages.append("You bump into the edge of the concrete grid.")
            self._send_osc_state(event="bump_edge")
            return

        target = self.get_monster_at(new_x, new_y)
        if target:
            dmg = self.player_attack_damage()
            target.hp -= dmg
            self.messages.append(f"You hit the {target.name} for {dmg} damage!")
            if target.hp <= 0:
                if target.name == "Nezha":
                    self.messages.append("Nezha discards this concrete body. The pattern sinks deeper.")
                    self.nezha_phase = min(self.nezha_phase + 1, 3)
                    self._osc_send("/event", "nezha_phase_end")
                else:
                    self.messages.append(f"The {target.name} dies.")
            self.monsters_take_turns()
            self._send_osc_state(event="player_attack")
            return

        tile = self.tiles[new_y][new_x]

        if tile == STAIRS_DOWN:
            self.messages.append("You step onto the stairwell and descend.")
            self.new_level(first=False)
            return

        if tile == FLOOR:
            self.player.x = new_x
            self.player.y = new_y
            self.check_pickup()
            self.monsters_take_turns()
            self._send_osc_state(event="player_move")
            return

        self.messages.append("Your shoulder hits raw concrete.")
        self._send_osc_state(event="bump_wall")

    def wait_turn(self) -> None:
        self.messages.append("You wait and feel the structure hum.")
        self.monsters_take_turns()
        self._send_osc_state(event="wait")

    def check_pickup(self) -> None:
        item = self.get_item_at(self.player.x, self.player.y)
        if not item:
            return

        self.items.remove(item)

        if item.kind == "weapon":
            self.inventory.append(item)
            self.messages.append(f"You pick up a {item.name}.")
            self.auto_equip_weapon(item)
            self._send_osc_state(event="pickup_weapon")
        elif item.kind == "potion":
            healed = min(MAX_HP - self.player.hp, item.power)
            if healed > 0:
                self.player.hp += healed
                self.messages.append(f"Concrete dust washes from your lungs. (+{healed} HP)")
            else:
                self.messages.append("You drink, but feel no different.")
            self._send_osc_state(event="pickup_potion")

    def auto_equip_weapon(self, new_weapon: Item) -> None:
        best = self.current_weapon
        if best is None or new_weapon.power > best.power:
            self.current_weapon = new_weapon
            self.messages.append(f"You wield the {new_weapon.name}.")

    def player_attack_damage(self) -> int:
        base = random.randint(1, 4)
        bonus = self.current_weapon.power if self.current_weapon else 0
        level_bonus = max(0, self.level - 1) // 2
        return base + bonus + level_bonus

    def get_monster_at(self, x: int, y: int) -> Optional[Entity]:
        for m in self.monsters:
            if m.x == x and m.y == y and m.hp > 0:
                return m
        return None

    # ---------------- MONSTERS (HALF SPEED) ----------------
    def monsters_take_turns(self) -> None:
        if self.player is None:
            return

        self._monster_turn_counter += 1
        if self._monster_turn_counter % 2 == 1:
            return  # half speed

        for m in self.monsters:
            if m.hp <= 0:
                continue

            # Nezha is more aggressive: stronger pull toward the player
            chase_bias = 0.9 if m.name == "Nezha" else 0.75

            if random.random() < chase_bias:
                dx = (self.player.x > m.x) - (self.player.x < m.x)
                dy = (self.player.y > m.y) - (self.player.y < m.y)
            else:
                dx, dy = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)])

            nx, ny = m.x + dx, m.y + dy

            if nx == self.player.x and ny == self.player.y:
                base = random.randint(1, 3) + max(0, self.level - 1) // 2
                if m.name == "Nezha":
                    base += 1 + self.nezha_phase  # a bit nastier
                dmg = base
                self.player.hp -= dmg
                self.messages.append(f"The {m.name} hits you for {dmg} damage!")
                if self.player.hp <= 0:
                    self.messages.append("You fall between slabs of concrete. Game over.")
                    self._send_osc_state(event="player_die")
                else:
                    self._send_osc_state(event="player_hit")
                continue

            if self.is_walkable_for_monster(nx, ny):
                m.x, m.y = nx, ny

        self.monsters = [m for m in self.monsters if m.hp > 0]

    def is_walkable_for_monster(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        if self.tiles[y][x] not in (FLOOR, STAIRS_DOWN):
            return False
        for o in self.monsters:
            if o.x == x and o.y == y and o.hp > 0:
                return False
        if self.player and self.player.x == x and self.player.y == y:
            return False
        return True

    # ---------------- OSC HELPERS ----------------
    def _osc_pack_string(self, s: str) -> bytes:
        data = s.encode("utf-8") + b"\0"
        while len(data) % 4 != 0:
            data += b"\0"
        return data

    def _osc_pack(self, address: str, *args: Any) -> bytes:
        blob = self._osc_pack_string(address)
        tags = ","
        for a in args:
            if isinstance(a, int):
                tags += "i"
            elif isinstance(a, float):
                tags += "f"
            else:
                tags += "s"
        blob += self._osc_pack_string(tags)
        for a in args:
            if isinstance(a, int):
                blob += struct.pack(">i", a)
            elif isinstance(a, float):
                blob += struct.pack(">f", float(a))
            else:
                blob += self._osc_pack_string(str(a))
        return blob

    def _osc_send(self, address: str, *args: Any) -> None:
        with suppress(Exception):
            packet = self._osc_pack(address, *args)
            self._osc_sock.sendto(packet, (self.osc_host, self.osc_port))

    def _send_osc_state(self, event: str = "") -> None:
        if not self.player:
            return
        self._osc_send("/player", int(self.player.x), int(self.player.y), int(self.player.hp))
        self._osc_send("/level", int(self.level))
        self._osc_send("/monsters", int(len(self.monsters)))
        if event:
            self._osc_send("/event", event)
        if self.messages:
            self._osc_send("/message", self.messages[-1])

    # ---------------- SERIALIZATION ----------------
    def serialize(self) -> Dict[str, Any]:
        return {
            "tiles": ["".join(r) for r in self.tiles],
            "player": {
                "x": self.player.x,
                "y": self.player.y,
                "char": self.player.char,
                "hp": self.player.hp,
            },
            "monsters": [
                {"x": m.x, "y": m.y, "char": m.char, "name": m.name, "hp": m.hp}
                for m in self.monsters
            ],
            "items": [
                {
                    "x": it.x,
                    "y": it.y,
                    "char": it.char,
                    "name": it.name,
                    "kind": it.kind,
                    "power": it.power,
                }
                for it in self.items
            ],
            "messages": self.messages[-10:],
            "game_over": self.player.hp <= 0,
            "level": self.level,
            "weapon": (
                {"name": self.current_weapon.name, "power": self.current_weapon.power}
                if self.current_weapon
                else None
            ),
            "inventory": [
                {"name": it.name, "kind": it.kind, "power": it.power}
                for it in self.inventory
            ],
        }
