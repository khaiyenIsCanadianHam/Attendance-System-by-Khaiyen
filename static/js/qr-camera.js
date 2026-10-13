"use strict";
/** Camera lifecycle and local QR decoding. No uploads or third-party requests. */
window.QrCamera = class {
    constructor(container, onDecode) {
        this.container = container;
        this.onDecode = onDecode;
        this.stream = null;
        this.timer = null;
        this.cancelled = false;
    }
    async start() {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera requires HTTPS");
        const stream = await navigator.mediaDevices.getUserMedia({audio:false, video:{facingMode:{ideal:"environment"},width:{ideal:1280},height:{ideal:720}}});
        if (this.cancelled) { stream.getTracks().forEach(track => track.stop()); return false; }
        this.stream = stream;
        const video = document.createElement("video");
        video.muted = true; video.autoplay = true; video.playsInline = true;
        video.style.width = "100%";
        video.srcObject = stream;
        this.container.replaceChildren(video);
        try { await video.play(); }
        catch (error) { await this.stop(); throw error; }
        if (this.cancelled) return false;
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d", {willReadFrequently:true});
        this.timer = setInterval(() => {
            if (video.readyState < 2 || !video.videoWidth) return;
            canvas.width = video.videoWidth; canvas.height = video.videoHeight;
            context.drawImage(video,0,0);
            const frame = context.getImageData(0,0,canvas.width,canvas.height);
            const code = jsQR(frame.data,canvas.width,canvas.height,{inversionAttempts:"attemptBoth"});
            if (code) this.onDecode(code.data);
        },200);
        return true;
    }
    async stop() {
        this.cancelled = true;
        clearInterval(this.timer);
        this.stream?.getTracks().forEach(track => track.stop());
        this.stream = null;
        this.container.replaceChildren();
    }
};
