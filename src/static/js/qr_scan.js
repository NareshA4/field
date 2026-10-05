/* M12 live camera scanner for FieldOps Nexus asset labels.
 *
 * - Opens the camera with the standard getUserMedia API (rear camera preferred on phones, camera switching when
 *   the device has several) and shows it in the scan frame on the page.
 * - Detects QR codes and Code 128 barcodes automatically: the browser's native BarcodeDetector when it supports
 *   them, otherwise the bundled ZXing decoder (@zxing/library, served from /static/lib, no CDN). BarcodeDetector is
 *   therefore an accelerator, never a requirement (Chrome Android, desktop Chrome / Edge / Firefox, Safari).
 * - The scanned text is only a label identifier. It is checked for shape here and then handed to the server
 *   (/app/s/<token>/?open=asset), which looks it up, enforces login, organization and site access, records the scan
 *   and opens the asset. Nothing is authorised by the browser.
 * - The manual code form on the page keeps working with or without a camera. */
(function () {
  "use strict";
  var root = document.getElementById("scan-camera");
  if (!root) { return; }

  var video = document.getElementById("scan-video");
  var statusEl = document.getElementById("scan-status");
  var errorBox = document.getElementById("scan-error");
  var retryBtn = document.getElementById("scan-retry");
  var switchBtn = document.getElementById("scan-switch");
  var stopBtn = document.getElementById("scan-stop");
  var viewport = document.getElementById("scan-viewport");
  var resolveBase = root.dataset.resolveUrl;      // ".../s/__TOKEN__/"
  var zxingSrc = root.dataset.zxingSrc;
  var TOKEN_RE = /^[A-Za-z0-9_-]{8,64}$/;

  var stream = null;
  var frameTimer = null;
  var detector = null;       // native BarcodeDetector when usable
  var zxing = null;          // { reader, hints } once the fallback is loaded
  var cameras = [];
  var currentDeviceId = null;
  var locked = false;        // set once a valid label was found: no second scan while navigating
  var lastInvalid = { text: "", at: 0 };
  var starting = false;
  var nativeFailures = 0;
  var canvas = document.createElement("canvas");
  var ctx = canvas.getContext("2d", { willReadFrequently: true });

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "fx-scan-status" + (kind ? " fx-scan-" + kind : "");
  }
  function showError(text, canRetry) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
    retryBtn.hidden = !canRetry;
  }

  /* ---- label text -> token ------------------------------------------------------------------------------- */
  function extractToken(text) {
    text = String(text || "").trim();
    var m = text.match(/\/s\/([A-Za-z0-9_-]{8,64})\/?(?:[?#].*)?$/);
    if (m) { return m[1]; }
    return TOKEN_RE.test(text) ? text : "";
  }

  /* ---- decoding ------------------------------------------------------------------------------------------ */
  function loadZxing() {
    if (zxing) { return Promise.resolve(zxing); }
    return new Promise(function (resolve, reject) {
      function ready() {
        var Z = window.ZXing;
        var hints = new Map();
        hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.CODE_128]);
        hints.set(Z.DecodeHintType.TRY_HARDER, true);
        zxing = { Z: Z, reader: new Z.MultiFormatReader(), hints: hints };
        resolve(zxing);
      }
      if (window.ZXing) { ready(); return; }
      var s = document.createElement("script");
      s.src = zxingSrc;
      s.onload = ready;
      s.onerror = function () { reject(new Error("decoder")); };
      document.head.appendChild(s);
    });
  }

  function nativeSupported() {
    if (!("BarcodeDetector" in window) || typeof window.BarcodeDetector.getSupportedFormats !== "function") {
      return Promise.resolve(false);
    }
    return window.BarcodeDetector.getSupportedFormats().then(function (formats) {
      var wanted = ["qr_code", "code_128"].filter(function (f) { return formats.indexOf(f) !== -1; });
      if (wanted.indexOf("qr_code") === -1) { return false; }
      detector = new window.BarcodeDetector({ formats: wanted });
      return true;
    }).catch(function () { return false; });
  }

  function decodeWithZxing() {
    var w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) { return null; }
    var scale = Math.min(1, 720 / w);
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    var Z = zxing.Z;
    try {
      var source = new Z.HTMLCanvasElementLuminanceSource(canvas);
      var bitmap = new Z.BinaryBitmap(new Z.HybridBinarizer(source));
      return zxing.reader.decode(bitmap, zxing.hints).getText();
    } catch (e) {
      return null;       // NotFoundException: nothing readable in this frame
    }
  }

  function scanFrame() {
    if (locked || !stream || video.readyState < 2) { return Promise.resolve(null); }
    if (detector) {
      return detector.detect(video).then(function (codes) {
        nativeFailures = 0;
        return codes.length ? codes[0].rawValue : null;
      }).catch(function () {
        // the native engine misbehaves on this device: switch to the bundled decoder for good
        if (++nativeFailures >= 3) { detector = null; return loadZxing().then(function () { return null; }); }
        return null;
      });
    }
    return Promise.resolve(zxing ? decodeWithZxing() : null);
  }

  function loop() {
    frameTimer = null;
    if (locked || !stream) { return; }
    scanFrame().then(function (text) {
      if (text) { handleDetected(text); }
    }).catch(function () { /* next frame */ }).then(function () {
      if (!locked && stream) { frameTimer = setTimeout(loop, 120); }
    });
  }

  function handleDetected(text) {
    var token = extractToken(text);
    if (!token) {
      var now = Date.now();
      if (text === lastInvalid.text && now - lastInvalid.at < 4000) { return; }
      lastInvalid = { text: text, at: now };
      setStatus("That code is not a FieldOps asset label. Keep scanning or enter the code below.", "warn");
      return;
    }
    if (locked) { return; }
    locked = true;                       // duplicate detections while the page navigates are ignored
    stopCamera();
    viewport.classList.add("fx-scan-found");
    setStatus("Label detected. Opening the asset…", "ok");
    showError("", false);
    window.location.assign(resolveBase.replace("__TOKEN__", encodeURIComponent(token)) + "?open=asset");
  }

  /* ---- camera -------------------------------------------------------------------------------------------- */
  function stopCamera() {
    if (frameTimer) { clearTimeout(frameTimer); frameTimer = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    video.srcObject = null;
  }

  function describe(err) {
    var name = err && err.name;
    if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
      return { text: "Camera permission was denied. Allow camera access for this site in the browser's site " +
        "settings (the lock / camera icon in the address bar), then press Retry camera. You can also enter the " +
        "code below.", retry: true };
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
      return { text: "No camera was found on this device. Enter the code below instead.", retry: true };
    }
    if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
      return { text: "The camera is in use by another app or tab, or could not be started. Close it and press " +
        "Retry camera.", retry: true };
    }
    return { text: "The camera could not be started. Press Retry camera or enter the code below.", retry: true };
  }

  function refreshCameras() {
    return navigator.mediaDevices.enumerateDevices().then(function (devices) {
      cameras = devices.filter(function (d) { return d.kind === "videoinput"; });
      switchBtn.hidden = cameras.length < 2;
    }).catch(function () { switchBtn.hidden = true; });
  }

  function open(constraints) {
    return navigator.mediaDevices.getUserMedia({ video: constraints, audio: false });
  }

  function start(deviceId) {
    if (starting || locked) { return; }
    starting = true;
    stopCamera();
    showError("", false);
    setStatus("Starting the camera… allow camera access if your browser asks.", "");
    var constraints = deviceId
      ? { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
      : { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } };
    open(constraints).catch(function (err) {
      if (err && err.name === "OverconstrainedError") { return open(true); }  // e.g. a desktop webcam
      throw err;
    }).then(function (s) {
      stream = s;
      var track = s.getVideoTracks()[0];
      var settings = (track && track.getSettings && track.getSettings()) || {};
      currentDeviceId = settings.deviceId || deviceId || null;
      viewport.classList.toggle("fx-scan-mirror", settings.facingMode === "user");
      video.srcObject = s;
      return video.play();
    }).then(function () {
      return Promise.all([refreshCameras(), nativeSupported()]);
    }).then(function (res) {
      root.dataset.engine = res[1] ? "native" : "zxing";   // which decoder is active (diagnostics / tests)
      if (window.console && console.info) { console.info("[fx-scanner] decoder: " + root.dataset.engine); }
      if (res[1]) { return null; }
      return loadZxing();       // no usable native detector: load the bundled decoder
    }).then(function () {
      viewport.hidden = false;
      stopBtn.hidden = false;
      setStatus("Point the camera at the FieldOps QR code on the asset label.", "");
      frameTimer = setTimeout(loop, 150);
    }).catch(function (err) {
      stopCamera();
      var d = (err && err.message === "decoder")
        ? { text: "The scanner component could not be loaded. Reload the page or enter the code below.", retry: true }
        : describe(err);
      setStatus("Camera unavailable.", "warn");
      showError(d.text, d.retry);
    }).then(function () { starting = false; });
  }

  function supported() {
    if (!window.isSecureContext) {
      return "The camera needs a secure (HTTPS) connection. Open this page over HTTPS or enter the code below.";
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return "This browser cannot open the camera. Enter the code below instead.";
    }
    return "";
  }

  retryBtn.addEventListener("click", function () { start(null); });
  switchBtn.addEventListener("click", function () {
    if (cameras.length < 2) { return; }
    var i = cameras.findIndex(function (c) { return c.deviceId === currentDeviceId; });
    start(cameras[(i + 1) % cameras.length].deviceId);
  });
  stopBtn.addEventListener("click", function () {
    stopCamera();
    stopBtn.hidden = true;
    retryBtn.hidden = false;
    retryBtn.textContent = "Start camera";
    setStatus("Camera stopped.", "");
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { stopCamera(); }
    else if (!locked && !stopBtn.hidden && !stream) { start(currentDeviceId); }
  });
  window.addEventListener("pagehide", stopCamera);

  var problem = supported();
  if (problem) {
    setStatus("Camera not available here.", "warn");
    showError(problem, false);
    return;
  }
  // Camera permission already refused: say so immediately (the Retry button asks again where the browser allows).
  if (navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: "camera" }).then(function (p) {
      if (p.state === "denied") {
        setStatus("Camera blocked.", "warn");
        showError(describe({ name: "NotAllowedError" }).text, true);
      } else { start(null); }
    }).catch(function () { start(null); });
  } else {
    start(null);
  }
})();
