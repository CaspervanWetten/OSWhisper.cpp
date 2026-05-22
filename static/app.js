const socket = new WebSocket(`ws://${window.location.host}/ws`);

socket.onopen = () => {
    console.log("WebSocket connection established");
    refreshFileList();
};

socket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    // Re-enable upload button if we get a status or error
    if (data.type === 'status' || data.type === 'error') {
        const submitBtn = document.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = false;
    }

    switch (data.type) {
        case 'status':
            updateStatus(data.message, 'success');
            refreshFileList();
            break;
        case 'error':
            updateStatus(data.message, 'danger');
            refreshFileList();
            break;
        case 'pong':
            // Heartbeat received
            break;
        default:
            console.warn("Unknown message type:", data.type);
    }
};

socket.onclose = () => {
    console.log("WebSocket connection closed");
    updateStatus("Connection lost. Please refresh.", "warning");
};

async function refreshFileList() {
    try {
        const response = await fetch('/files');
        const files = await response.json();
        const tbody = document.getElementById('file-list');
        tbody.innerHTML = '';

        files.forEach(file => {
            const tr = document.createElement('tr');
            
            // Filename cell
            const tdName = document.createElement('td');
            tdName.textContent = file.filename;
            tr.appendChild(tdName);

            // Stage/Status cell
            const tdStage = document.createElement('td');
            let stageBadge = '';
            if (file.status === 'transcription') {
                stageBadge = '<span class="badge bg-success">Transcription</span>';
            } else if (file.status === 'transcribing') {
                stageBadge = '<span class="badge bg-warning text-dark">Transcribing...</span> <div class="spinner-grow spinner-grow-sm text-warning" role="status"></div>';
            } else if (file.status === 'queued') {
                stageBadge = '<span class="badge bg-info text-dark">In Queue</span> <div class="spinner-border spinner-border-sm text-info" role="status"></div>';
            } else if (file.status === 'encoded') {
                stageBadge = '<span class="badge bg-primary">Encoded</span>';
            } else {
                stageBadge = '<span class="badge bg-secondary">Uploaded</span>';
            }
            tdStage.innerHTML = stageBadge;
            tr.appendChild(tdStage);

            // Action cell
            const tdAction = document.createElement('td');
            if (file.status === 'transcription') {
                tdAction.innerHTML = `<a href="/download/${file.filename}" class="btn btn-sm btn-success" download="${file.filename}">Download TXT</a>`;
            } else if (file.status === 'encoded') {
                const btnQueue = document.createElement('button');
                btnQueue.className = 'btn btn-sm btn-outline-info';
                btnQueue.textContent = 'Queue';
                btnQueue.onclick = () => {
                    // Send queue message to backend via WebSocket
                    socket.send(JSON.stringify({
                        type: 'queue',
                        filename: file.filename
                    }));
                    // Update UI locally for immediate feedback
                    tdStage.innerHTML = '<span class="badge bg-info text-dark">In Queue</span> <div class="spinner-border spinner-border-sm text-info" role="status"></div>';
                    tdAction.innerHTML = '';
                };
                tdAction.appendChild(btnQueue);
            } else {
                // No spinner in action cell anymore, handled in stage badge
                tdAction.innerHTML = '';
            }
            tr.appendChild(tdAction);

            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error("Error refreshing file list:", error);
    }
}

async function uploadFile() {
    const fileInput = document.getElementById('file');
    const submitBtn = document.querySelector('button[type="submit"]');
    const file = fileInput.files[0];
    
    if (!file) {
        updateStatus("Please select a file first", "warning");
        return;
    }

    const allowed = ['mp3', 'mp4', 'mkv', 'wav', "m4a"];
    if (!allowed.includes(file.name.split('.').pop().toLowerCase())) {
        updateStatus("Please upload a supported file (.mp3, .mp4, .mkv, .wav, m4a)", "warning");
        return;
    }

    submitBtn.disabled = true;
    updateStatus(`Uploading ${file.name}...`, "info");

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/upload', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (result.error) {
            updateStatus(`Upload failed: ${result.error}`, "danger");
            submitBtn.disabled = false;
        } else {
            updateStatus(`Uploaded ${file.name}. Encoding started.`, "success");
            refreshFileList();
            // Button stays disabled until socket message says encoding is done or errored, 
            // or we can re-enable it if we want to allow multiple uploads.
            // For now, let's re-enable it.
            submitBtn.disabled = false;
        }
    } catch (error) {
        console.error("Upload error:", error);
        updateStatus("Error uploading file", "danger");
        submitBtn.disabled = false;
    }
}


function updateStatus(message, type) {
    const statusDiv = document.getElementById('status');
    statusDiv.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
}

// Bind the button click
document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('form');
    form.onsubmit = (e) => {
        e.preventDefault();
        uploadFile();
    };
    refreshFileList();
    
    // Refresh the file list every 3.5 seconds
    // setInterval(refreshFileList, 3500);

    // Heartbeat ping every 30 seconds
    setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 30000);
});
