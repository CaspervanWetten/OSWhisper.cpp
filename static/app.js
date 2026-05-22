const socket = new WebSocket(`ws://${window.location.host}/ws`);

socket.onopen = () => {
    console.log("WebSocket connection established");
};

socket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    // Match-Case pattern (using switch statement)
    switch (data.type) {
        case 'status':
            updateStatus(data.message, 'success');
            break;
        case 'error':
            updateStatus(data.message, 'danger');
            break;
        case 'pong':
            console.log("Server responded to ping");
            break;
        default:
            console.warn("Unknown message type:", data.type);
    }
};

socket.onclose = () => {
    console.log("WebSocket connection closed");
    updateStatus("Connection lost. Please refresh.", "warning");
};

function uploadFile() {
    const fileInput = document.getElementById('file');
    const file = fileInput.files[0];
    
    if (!file) {
        updateStatus("Please select a file first", "warning");
        return;
    }

    const allowed = ['mp3', 'mp4', 'mkv'];
    if (!allowed.includes(file.name.split('.').pop().toLowerCase())) {
        updateStatus("Please upload either a .mp3, .mp4 or a .mkv", "warning");
        return;
    }

    const reader = new FileReader();
    reader.onload = () => {
        const content = reader.result.split(',')[1]; // Get base64 part
        const message = {
            type: 'upload',
            filename: file.name,
            content: content
        };
        socket.send(JSON.stringify(message));
        updateStatus(`Uploading ${file.name}...`, "info");
    };
    reader.onerror = () => {
        updateStatus("Error reading file", "danger");
    };
    reader.readAsDataURL(file);
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
});
