import asyncio
import websockets
import random
from difflib import SequenceMatcher

# Gerenciar salas e palavras
rooms = {}
words = [
    "banana", "maçã", "abacaxi", "laranja", "uva", "melancia", "morango", "pêra",
    "manga", "kiwi", "cereja", "ameixa", "pêssego", "figo", "limão", "tangerina",
    "framboesa", "groselha", "goiaba", "maracujá", "caju", "jabuticaba", "carambola", 
    "pitanga", "acerola", "coco", "graviola", "cacau", "amora", "mamão", 
    "abiu", "jaca", "seriguela", "umbu", "nectarina", "mirtilo", "mexerica", 
    "damasco", "physalis", "noni", "castanha", "buriti", "cupuaçu", "tamarindo",
    "sapoti", "ata", "araruta", "babaçu", "baru", "jenipapo", "pequi"
]

async def handle_connection(websocket, path):
    path_parts = path.strip("/").split("/")
    print(f"Caminho processado: {path_parts}")

    if len(path_parts) not in [2, 3]:
        await websocket.close(code=1003, reason="Caminho inválido.")
        print(f"Conexão encerrada: caminho inválido -> {path_parts}")
        return

    room_id, player_name = path_parts[-2:]
    if room_id not in rooms:
        rooms[room_id] = {
            "players": [],
            "current_drawer": None,
            "word": None,
            "hint": None,
            "timer_task": None,
            "rounds_left": 10,
            "scores": {},
            "in_tiebreaker": False,
            "guessed_players": [],
            "drawing": []  # Lista para armazenar os traços do desenho
        }

    room = rooms[room_id]
    room["players"].append({"name": player_name, "websocket": websocket})
    room["scores"][player_name] = 0
    await broadcast(room, f"{player_name} entrou na sala!")

    if len(room["players"]) >= 2 and not room["timer_task"]:
        await asyncio.sleep(3)
        room["timer_task"] = asyncio.create_task(game_loop(room))

    try:
        while True:
            message = await websocket.recv()

            if message.startswith("draw:"):
                if room["current_drawer"] and room["current_drawer"]["websocket"] == websocket:
                    if message == "draw:break":
                        room["drawing"].append("break")  # Indica que o traço foi interrompido
                    else:
                        room["drawing"].append(message)
                    await broadcast(room, message, exclude=websocket)
                continue

            if room["current_drawer"] and room["current_drawer"]["websocket"] == websocket:
                continue

            if room["word"]:
                if message.lower() == room["word"]:
                    if player_name not in room["guessed_players"]:
                        room["guessed_players"].append(player_name)
                        guessed_player_count = len(room["guessed_players"])
                        if guessed_player_count == 1:
                            room["scores"][player_name] += 10
                            room["scores"][room["current_drawer"]["name"]] += 5
                            await broadcast(room, f"{player_name} adivinhou! e ganhou 10 pontos e o {room['current_drawer']['name']} ganhou 5 pontos!")
                        else:
                            room["scores"][player_name] += max(10 - guessed_player_count, 1)
                            await broadcast(room, f"✅ {player_name} adivinhou! {player_name} +{max(10 - guessed_player_count, 1)} pontos!")
                    if len(room["guessed_players"]) == len(room["players"]) - 1:
                        
                        room["guessed_players"] = []
                    continue
                similarity = SequenceMatcher(None, message.lower(), room["word"]).ratio()
                if similarity > 0.7:
                    await websocket.send("Passou perto! é quase isso...")
            await broadcast(room, f"{player_name}: {message}")

    except websockets.ConnectionClosed:
        print(f"Conexão fechada: {player_name}")
    finally:
        room["players"] = [p for p in room["players"] if p["websocket"] != websocket]
        await broadcast(room, f"{player_name} saiu da sala.")
        if not room["players"]:
            del rooms[room_id]
        elif len(room["players"]) < 2 and room["timer_task"]:
            room["timer_task"].cancel()
            room["timer_task"] = None

async def game_loop(room):
    last_drawer = None
    while room["rounds_left"] > 0 or room["in_tiebreaker"]:
        if not room["in_tiebreaker"]:
            room["rounds_left"] -= 1

        eligible_players = [p for p in room["players"] if p != last_drawer]
        if not eligible_players:
            eligible_players = room["players"]

        room["current_drawer"] = random.choice(eligible_players)
        last_drawer = room["current_drawer"]

        drawer = room["current_drawer"]
        room["word"] = random.choice(words)
        room["hint"] = " ".join("_" if c != " " else " " for c in room["word"])

        await drawer["websocket"].send(f"Você está desenhando: {room['word']}")
        await broadcast(room, f"{drawer['name']} está desenhando. Dica: {room['hint']}", exclude=drawer["websocket"])

        for i in range(61, -1, -1):
            await broadcast(room, f"tempo:{i}")
            await asyncio.sleep(1)

        await broadcast(room, "clear_canvas")  # Comando para limpar a tela no front-end
        room["drawing"] = []
        if len(room["guessed_players"]) < len(room["players"]) - 1:
            await broadcast(room, f"Tempo esgotado! A palavra era: {room['word']}")
            await asyncio.sleep(10)

        room["word"] = None
        room["guessed_players"] = []
        if room["rounds_left"] == 0 and not room["in_tiebreaker"]:
            await handle_end_of_game(room)
            if not room["in_tiebreaker"]:
                return
    if room["in_tiebreaker"]:
        await broadcast(room, "Desempate final concluído!")
        await handle_end_of_game(room)

async def handle_end_of_game(room):
    ranking = sorted(room["scores"].items(), key=lambda x: x[1], reverse=True)
    max_score = ranking[0][1]
    tied_players = [player for player, score in ranking if score == max_score]

    if len(tied_players) > 1:
        await broadcast(room, f"Empate entre: {', '.join(tied_players)}. Iniciando rodada de desempate!")
        room["in_tiebreaker"] = True
        room["players"] = [p for p in room["players"] if p["name"] in tied_players]
        room["rounds_left"] = 1
    else:
        room["in_tiebreaker"] = False
        ranking_message = "Ranking Final:\n" + "\n".join([f"{i+1}. {player}: {score} pontos" for i, (player, score) in enumerate(ranking)])
        await broadcast(room, ranking_message)
        del rooms[room["room_id"]]

async def broadcast(room, message, exclude=None):
    for player in room["players"]:
        if player["websocket"] != exclude:
            try:
                await player["websocket"].send(message)
            except websockets.ConnectionClosed:
                continue

start_server = websockets.serve(handle_connection, "0.0.0.0", 8000)
print("Servidor iniciado em ws://localhost:8000")
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
