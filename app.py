import os
import shutil
import subprocess
import base64
import asyncio
import uuid
import logging
import json
import requests
import zipfile
from fastapi import FastAPI, UploadFile, File, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub import hf_hub_download

# Ensure functionality directories exist
os.makedirs("uploads", exist_ok=True)
os.makedirs("transcriptions", exist_ok=True)
os.makedirs("encoded", exist_ok=True)
os.makedirs("queue", exist_ok=True)
os.makedirs("static", exist_ok=True)



class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to connection: {e}")

manager = ConnectionManager()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Global queue for transcription tasks
transcription_queue = asyncio.Queue()
currently_transcribing = set() # Track files actively being processed

async def transcription_worker():
    """
    Background worker that processes the transcription queue one by one.
    """
    while True:
        task_data = await transcription_queue.get()
        encoded_path, file_id_base = task_data
        currently_transcribing.add(file_id_base)
        
        try:
            await transcribe_audio(encoded_path)
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            currently_transcribing.discard(file_id_base)
            transcription_queue.task_done()

@app.on_event("startup")
async def startup_event():
    # Check for a folder called "Release-1.8.4" (the whisper.cpp)
    if not os.path.exists("ggml-large-v3-turbo.bin"):
        logger.log("whisper model not found, preparing download...")
        model_path = hf_hub_download(
            repo_id="ggerganov/whisper.cpp",
            local_dir="./",
            filename="ggml-large-v3-turbo.bin"  # Target the specific model directory
        )
        logger.log(f"downloaded model to: {model_path}")


    # Check for (and download) the whisper.cpp cli
    if not os.path.exists("Release/whisper-cli.exe"):
        logger.log("whisper-cli.exe not found, downloading whisper.cpp release 1.8.4...")
        
        release_url = "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.4/whisper-bin-x64.zip"
        zip_path = "whisper-1.8.4.zip"
        
        response = requests.get(release_url, stream=True)
        response.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.log("Download complete, unpacking...")
        
        # Unpack into Release/ folder
        os.makedirs("Release", exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for member in zip_ref.namelist():
                # Strip any top-level folder from the zip and extract flat into Release/
                filename = os.path.basename(member)
                if not filename:
                    continue
                source = zip_ref.open(member)
                target_path = os.path.join("Release", filename)
                with open(target_path, "wb") as target:
                    target.write(source.read())
        
        try:
            os.remove(zip_path)
        except Exception as e:
            logger.log(f"zip removal error: {e}")
        logger.log("whisper-cli.exe ready in Release/")


    asyncio.create_task(transcription_worker())

@app.on_event("shutdown")
def cleanup_queue():
    queue_dir = "./queue"
    if os.path.exists(queue_dir):
        shutil.rmtree(queue_dir)
        os.makedirs(queue_dir)  # Recreate empty dir


async def transcribe_audio(encoded_path: str):
    """
    Asynchronously runs whisper-cli.exe to transcribe the encoded audio.
    """
    filename_base = os.path.splitext(os.path.basename(encoded_path))[0]
    output_base = os.path.join("transcriptions", filename_base)
    
    command = [
        "Release/whisper-cli.exe",
        "-m", "ggml-large-v3-turbo.bin",
        "-f", encoded_path,
        "-l", "nl",
        "-otxt",
        "-of", output_base
    ]

    logger.info(f"starting transcription of {encoded_path}")
    
    try:      
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"Transcribed file {encoded_path} to .txt")
            # Cleanup queue file after success
            if os.path.exists(encoded_path):
                os.remove(encoded_path)
            await manager.broadcast({
                "type": "status",
                "message": f"Transcription complete for {filename_base}.",
                "status": "transcribed"
            })
        else:
            logger.error(f"Whisper error: {stderr.decode()}")
            await manager.broadcast({
                "type": "error",
                "message": f"Whisper error: {stderr.decode()}"
            })

    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        await manager.broadcast({
            "type": "error",
            "message": f"Transcription error: {str(e)}"
        })

async def queue_file(filename_base: str):
    """
    Moves an encoded file to the queue and adds it to the transcription queue.
    """
    input_filename = f"{filename_base}.wav"
    input_path = os.path.join("encoded", input_filename)
    queue_path = os.path.join("queue", input_filename)
    
    if os.path.exists(input_path):
        shutil.copy(input_path, queue_path)
        await transcription_queue.put((queue_path, filename_base))
        logger.info(f"Queued file: {filename_base}")
    else:
        logger.error(f"File not found for queueing: {input_path}")



async def encode_audio(input_path: str, output_path: str):
    """
    Asynchronously runs ffmpeg to convert input to 16kHz mono wav.
    Handles errors and notifies the frontend via websocket.
    """
    try:
        command = [
            "ffmpeg", "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"re-encoded file {input_path} to 16khz .wav")
            if os.path.exists(input_path):
                os.remove(input_path)
            
            await manager.broadcast({
                "type": "status",
                "message": f"Encoding complete for {os.path.basename(output_path)}.",
                "status": "encoded"
            })
        else:
            error_msg = stderr.decode().split('\n')[-2] if stderr else "Unknown ffmpeg error"
            logger.error(f"ffmpeg error re-encoding {input_path}: {error_msg}")
            
            # Clean up input path even on failure to avoid clutter
            if os.path.exists(input_path):
                os.remove(input_path)

            await manager.broadcast({
                "type": "error",
                "message": f"FFmpeg failed: {error_msg}"
            })

    except Exception as e:
        logger.error(f"Unexpected error in encode_audio: {e}")
        if os.path.exists(input_path):
            os.remove(input_path)
        await manager.broadcast({
            "type": "error",
            "message": f"Encoding failed: {str(e)}"
        })

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, name="index.html")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename
    logger.info(f"received file {filename} via HTTP")
    
    if not filename:
        return {"error": "No filename provided"}

    file_id = str(uuid.uuid4())[:8]
    safe_filename = f"{file_id}_{filename}"
    filepath = os.path.join("uploads", safe_filename)
    
    try:
        with open(filepath, "wb") as f:
            shutil.copyfileobj(file.file, f)
        
        output_filename = f"{file_id}_{os.path.splitext(filename)[0]}.wav"
        output_path = os.path.join("encoded", output_filename)
        
        await manager.broadcast({"type": "status", "message": f"Uploaded {filename}. Encoding..."})
        asyncio.create_task(encode_audio(filepath, output_path))
        
        return {"status": "ok", "filename": filename, "file_id": file_id}
    except Exception as e:
        logger.error(f"Upload processing error: {e}")
        return {"error": str(e)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                data = json.loads(data)
                message_type = data.get("type")

                match message_type:
                    case "upload":
                        # Legacy/small file upload via WS (optional, but we'll prioritize HTTP)
                        filename = data.get("filename")
                        content_b64 = data.get("content")
                        logger.info(f"received file {filename} via WS")

                        if filename and content_b64:
                            file_id = str(uuid.uuid4())[:8]
                            safe_filename = f"{file_id}_{filename}"
                            filepath = os.path.join("uploads", safe_filename)
                            try:
                                content = base64.b64decode(content_b64)
                                with open(filepath, "wb") as f:
                                    f.write(content)
                                output_filename = f"{file_id}_{os.path.splitext(filename)[0]}.wav"
                                output_path = os.path.join("encoded", output_filename)
                                await manager.broadcast({"type": "status", "message": f"Uploaded {filename}. Encoding..."})
                                asyncio.create_task(encode_audio(filepath, output_path))
                            except Exception as e:
                                logger.error(f"Upload processing error: {e}")
                                await websocket.send_json({"type": "error", "message": f"Failed to process upload: {str(e)}"})
                    case "queue":
                        filename = data.get("filename")
                        if filename:
                            filename_base = os.path.splitext(filename)[0]
                            await queue_file(filename_base)
                            await manager.broadcast({"type": "status", "message": f"Queued {filename}"})
                    case "ping":
                        await websocket.send_json({"type": "pong"})
            except (ValueError, json.JSONDecodeError) as e:
                logger.error(f"Invalid JSON received: {e}")
                continue
            except Exception as e:
                if isinstance(e, WebSocketDisconnect):
                    raise
                logger.error(f"Error processing websocket message: {e}")
                try:
                    await websocket.send_json({"type": "error", "message": "Internal server error processing message"})
                except:
                    pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected")
    except Exception as e:
        manager.disconnect(websocket)
        logger.error(f"WS Error: {e}")

@app.get("/files")
@app.get("/list_files")
async def list_files():
    file_status = {}
    dirs = [
        ("uploads", "uploaded"),
        ("encoded", "encoded"),
        ("queue", "queued"),
        ("transcriptions", "transcription")
    ]
    for folder, status in dirs:
        if not os.path.exists(folder): continue
        for f in os.listdir(folder):
            base, ext = os.path.splitext(f)
            if folder == "transcriptions" and ext != ".txt": continue
            effective_status = status
            if base in currently_transcribing:
                effective_status = "transcribing"
            file_status[base] = {"filename": f, "status": effective_status, "folder": folder}
    
    result = list(file_status.values())
    result.sort(key=lambda x: x['filename'], reverse=True)
    return result

@app.get("/download/{filename}")
async def download_transcription(filename: str):
    paths = [os.path.join("transcriptions", filename), os.path.join("encoded", filename)]
    for path in paths:
        if os.path.exists(path):
            return FileResponse(path=path, filename=filename, media_type='application/octet-stream')
    return {"error": "File not found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
