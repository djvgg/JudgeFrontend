import asyncio
import websockets
import json

async def simulate_tournament_progression():
    """
    Simulates a full tournament progression by winning matches through WebSocket.
    """
    uri = "ws://localhost:5001/ws"
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to Tournament WebSocket")
            
            # --- Round 1: Matches 1 to 8 ---
            for match_id in range(1, 9):
                print(f"Propagating Winner for Match #{match_id} (Round 1)")
                # Assign 10 points (Auto-win condition)
                await websocket.send(json.dumps({"type": "SCORE_UPDATE", "matchId": match_id, "playerNum": 1, "scoreType": "points", "value": 10}))
                await asyncio.sleep(0.3)
                await websocket.send(json.dumps({"type": "STATUS_UPDATE", "matchId": match_id, "status": "finished"}))
                await asyncio.sleep(0.5)

            # --- Round 2: Matches 9 to 12 ---
            for match_id in range(9, 13):
                print(f"Propagating Winner for Match #{match_id} (Round 2)")
                await websocket.send(json.dumps({"type": "SCORE_UPDATE", "matchId": match_id, "playerNum": 1, "scoreType": "points", "value": 10}))
                await asyncio.sleep(0.3)
                await websocket.send(json.dumps({"type": "STATUS_UPDATE", "matchId": match_id, "status": "finished"}))
                await asyncio.sleep(0.5)

            # --- Semi-finals: Matches 13 to 14 ---
            for match_id in range(13, 15):
                print(f"Propagating Winner for Semi-final Match #{match_id}")
                await websocket.send(json.dumps({"type": "SCORE_UPDATE", "matchId": match_id, "playerNum": 1, "scoreType": "points", "value": 10}))
                await asyncio.sleep(0.3)
                await websocket.send(json.dumps({"type": "STATUS_UPDATE", "matchId": match_id, "status": "finished"}))
                await asyncio.sleep(0.5)

            # --- Final: Match 15 ---
            print("Finishing Grand Final Match #15")
            await websocket.send(json.dumps({"type": "SCORE_UPDATE", "matchId": 15, "playerNum": 1, "scoreType": "points", "value": 10}))
            await asyncio.sleep(0.3)
            await websocket.send(json.dumps({"type": "STATUS_UPDATE", "matchId": 15, "status": "finished"}))
            
            print("\nSimulation complete. The tournament tree is now fully updated!")

    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    asyncio.run(simulate_tournament_progression())
