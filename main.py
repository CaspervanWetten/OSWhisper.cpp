import os
import shutil
import subprocess
import base64
import asyncio
import uuid
from fastapi import FastAPI, UploadFile, File, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Ensure directories exist
os.makedirs("uploads", exist_ok=True)
os.makedirs("transcriptions", exist_ok=True)
os.makedirs("encoded", exist_ok=True)
os.makedirs("static", exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

async def encode_audio(input_path: str, output_path: str, websocket: WebSocket):
    """
    Asynchronously runs ffmpeg to convert input to 16kHz mono wav.
    """
    command = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        output_path
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            await websocket.send_json({
                "type": "status",
                "message": f"Encoding complete: {os.path.basename(output_path)}",
                "status": "completed",
                "encoded_file": os.path.basename(output_path)
            })
        else:
            error_msg = stderr.decode()
            await websocket.send_json({
                "type": "error",
                "message": f"FFmpeg error: {error_msg}"
            })
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": f"Processing error: {str(e)}"
        })

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, name="index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            
            message_type = data.get("type")
            match message_type:
                case "upload":
                    filename = data.get("filename")
                    content_b64 = data.get("content")
                    if filename and content_b64:
                        # Use UUID to prevent conflicts
                        file_id = str(uuid.uuid4())[:8]
                        safe_filename = f"{file_id}_{filename}"
                        filepath = os.path.join("uploads", safe_filename)
                        
                        content = base64.b64decode(content_b64)
                        with open(filepath, "wb") as f:
                            f.write(content)
                        
                        # Prepare output path in /encoded
                        output_filename = f"{file_id}_{os.path.splitext(filename)[0]}.wav"
                        output_path = os.path.join("encoded", output_filename)
                        
                        # Send acknowledgement
                        await websocket.send_json({
                            "type": "status", 
                            "message": f"Uploaded {filename}. Starting encoding...",
                            "filename": filename
                        })
                        
                        # Run encoding in background (non-blocking)
                        asyncio.create_task(encode_audio(filepath, output_path, websocket))
                        
                case "ping":
                    await websocket.send_json({"type": "pong"})
                case _:
                    await websocket.send_json({"type": "error", "message": f"Unknown action: {message_type}"})
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass

@app.get("/download/{filename}")
async def download_transcription(filename: str):
    # Search in both transcriptions and encoded for convenience
    paths = [
        os.path.join("transcriptions", filename),
        os.path.join("encoded", filename)
    ]
    for path in paths:
        if os.path.exists(path):
            return FileResponse(
                path=path,
                filename=filename,
                media_type='application/octet-stream'
            )
    return {"error": "File not found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
