from flask import Flask, jsonify, request
from game import Game

app = Flask(__name__, static_folder="static", static_url_path="/static")

game = Game()

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/state")
def state():
    return jsonify(game.serialize())

@app.route("/command", methods=["POST"])
def command():
    global game
    data = request.get_json(force=True)
    cmd = data.get("command")
    if game.player.hp <= 0:
        game = Game()
        return jsonify(game.serialize())
    moves = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    if cmd in moves:
        dx, dy = moves[cmd]
        game.move_player(dx, dy)
    elif cmd == "wait":
        game.wait_turn()
    elif cmd == "restart":
        game = Game()
    return jsonify(game.serialize())

if __name__ == "__main__":
    app.run(debug=True)
