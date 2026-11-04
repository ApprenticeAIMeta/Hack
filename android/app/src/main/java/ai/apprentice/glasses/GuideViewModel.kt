package ai.apprentice.glasses

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioAttributes
import android.media.MediaPlayer
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import java.io.File
import android.media.MediaRecorder
import android.util.Base64
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.types.PhotoData
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.StreamState
import com.meta.wearable.dat.camera.types.VideoFrame
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.meta.wearable.dat.core.types.RegistrationState
import java.io.ByteArrayOutputStream
import java.util.ArrayDeque
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject

data class GuideUi(
    val registration: RegistrationState = RegistrationState.UNAVAILABLE,
    val hasGlasses: Boolean = false,
    val server: String = BuildConfig.DEFAULT_SERVER,
    val procedure: String = "airplane",
    val lang: String = "en",
    val asking: Boolean = false,
    val run: String = "",
    val running: Boolean = false,
    val status: String = "Pair the glasses to begin",
    val framesSent: Int = 0,
)

/**
 * Glasses camera -> server, expert's voice -> glasses speakers.
 *
 * Streams the glasses camera as compressed HEVC (about 55 kbps over Bluetooth, 7 fps) straight
 * to the ApprenticeAI server, which decodes and runs the guide. The server answers with clips of
 * the expert's recorded voice; they play through the phone's audio output, which is the glasses
 * while they are connected. Sound only complements the guide screen, which shows the same guidance.
 */
class GuideViewModel(app: Application) : AndroidViewModel(app) {
  private val _ui = MutableStateFlow(GuideUi())
  val ui: StateFlow<GuideUi> = _ui

  // The selector needs Wearables.initialize, which runs only after Android grants Bluetooth
  private val selector by lazy { AutoDeviceSelector() }
  private val http = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()
  private var socket: WebSocket? = null
  private var session: DeviceSession? = null
  private var camera: Camera? = null
  private var stream: com.meta.wearable.dat.camera.Stream? = null
  private val clips = ArrayDeque<String>()
  @Volatile private var player: MediaPlayer? = null

  fun onSdkReady() {
    viewModelScope.launch { Wearables.registrationState.collect { r -> _ui.update { it.copy(registration = r) } } }
    viewModelScope.launch { selector.activeDeviceFlow().collect { d -> _ui.update { it.copy(hasGlasses = d != null) } } }
  }

  fun setServer(s: String) = _ui.update { it.copy(server = s.trim()) }

  fun setProcedure(p: String) = _ui.update { it.copy(procedure = p.trim()) }

  fun setLang(l: String) = _ui.update { it.copy(lang = l.trim().lowercase()) }

  fun setRun(r: String) = _ui.update { it.copy(run = r) }

  fun status(s: String) = _ui.update { it.copy(status = s) }

  fun start(requestCamera: suspend (Permission) -> PermissionStatus) {
    if (_ui.value.running) return
    _ui.update { it.copy(running = true, framesSent = 0, status = "Connecting to the server") }
    viewModelScope.launch {
      val granted =
          Wearables.checkPermissionStatus(Permission.CAMERA).getOrDefault(PermissionStatus.Denied) ==
              PermissionStatus.Granted || requestCamera(Permission.CAMERA) == PermissionStatus.Granted
      if (!granted) return@launch fail("Allow camera access for Apprentice in the Meta AI app")
      openSocket()
      Wearables.createSession(selector)
          .onSuccess { s ->
            session = s
            viewModelScope.launch {
              s.state.collect { st ->
                if (st == DeviceSessionState.STOPPED && _ui.value.running) fail("Glasses session ended")
              }
            }
            viewModelScope.launch { s.errors.collect { e -> Log.e(TAG, "session: ${e.description}") } }
            s.start()
            viewModelScope.launch {
              s.state.first { it == DeviceSessionState.STARTED }
              startCamera(s)
            }
          }
          .onFailure { e, _ -> fail("Could not reach the glasses: ${e.description}") }
    }
  }

  private fun startCamera(s: DeviceSession) {
    s.addCamera(StreamConfiguration(videoQuality = VideoQuality.HIGH, frameRate = FRAME_RATE, compressVideo = true))
        .onSuccess { cam ->
          camera = cam
          val stream = cam.stream
          this.stream = stream
          viewModelScope.launch(Dispatchers.Default) { stream.videoStream.collect { send(it) } }
          viewModelScope.launch {
            stream.state.collect { st ->
              if (st == StreamState.STREAMING) status("Guiding. Start working.")
            }
          }
          stream.start().onFailure { e, _ -> fail("Camera did not start: ${e.description}") }
        }
        .onFailure { e, _ -> fail("Camera unavailable: ${e.description}") }
  }

  private fun send(frame: VideoFrame) {
    if (!frame.isCompressed) return
    val ws = socket ?: return
    val buf = frame.buffer
    val bytes = ByteArray(buf.remaining())
    buf.duplicate().get(bytes)
    if (frame.isCodecConfig || _ui.value.framesSent == 0) {
      ws.send(JSONObject().put("type", "config").put("codec", "hevc")
          .put("width", frame.width).put("height", frame.height).put("fps", FRAME_RATE).put("photos", true).toString())
    }
    ws.send(bytes.toByteString())
    _ui.update { it.copy(framesSent = it.framesSent + 1) }
  }

  private fun openSocket() {
    val u = _ui.value
    // The server records every session under this run name (out/sessions/<time>_<run>)
    val run = java.net.URLEncoder.encode(u.run.trim().ifEmpty { "run" }, "UTF-8")
    val req = Request.Builder().url("${u.server}/ws/device?procedure=${u.procedure}&run=$run&lang=${u.lang}").build()
    socket = http.newWebSocket(req, object : WebSocketListener() {
      override fun onMessage(webSocket: WebSocket, text: String) {
        val m = JSONObject(text)
        if (m.optString("type") == "snap") {
          snap(webSocket, m.getInt("id"))
          return
        }
        if (m.optString("type") == "play") {
          enqueue(u.server.replaceFirst("ws", "http") + m.getString("url"))
        }
      }

      override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        if (_ui.value.running) fail("Server connection lost: ${t.message}")
      }
    })
  }

  /** Takes a still during the stream for a Spark check; the server falls back to stream frames if it fails. */
  private fun snap(ws: WebSocket, id: Int) {
    val s = stream
    viewModelScope.launch(Dispatchers.Default) {
      val answer = JSONObject().put("type", "photo").put("id", id)
      // The glasses play a shutter sound on capture, which talks over the guidance; hold the still
      // until the current line has finished (the server waits up to 8 s before using a stream frame)
      var waited = 0
      while (player != null && waited < 2500) { delay(100); waited += 100 }
      s?.capturePhoto()
          ?.onSuccess { photo -> jpegOf(photo)?.let { answer.put("jpeg", Base64.encodeToString(it, Base64.NO_WRAP)) } }
          ?.onFailure { e, _ -> Log.w(TAG, "capture: $e") }
      ws.send(answer.toString())
    }
  }

  private fun jpegOf(photo: PhotoData): ByteArray? {
    val bitmap =
        when (photo) {
          is PhotoData.Bitmap -> photo.bitmap
          is PhotoData.HEIC -> {
            val bytes = ByteArray(photo.data.remaining())
            photo.data.duplicate().get(bytes)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
          }
        } ?: return null
    return ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.JPEG, 90, it) }.toByteArray()
  }

  /** Clips play one after another; a warning jumps the queue. */
  @Synchronized
  private fun enqueue(url: String) {
    clips.addLast(url)
    if (player == null) playNext()
  }

  @Synchronized
  private fun playNext() {
    val url = clips.pollFirst() ?: run { player = null; return }
    player = MediaPlayer().apply {
      setAudioAttributes(AudioAttributes.Builder()
          .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
          .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
      setDataSource(url)
      setOnPreparedListener { it.start() }
      setOnCompletionListener { it.release(); playNext() }
      setOnErrorListener { mp, _, _ -> mp.release(); playNext(); true }
      prepareAsync()
    }
  }

  // -- "hold to ask": record on the phone's mic (never the glasses' mic, which ends the stream), post the clip --
  private var recorder: MediaRecorder? = null
  private var askFile: File? = null

  fun startAsking() {
    if (recorder != null || !_ui.value.running) return
    val f = File(getApplication<Application>().cacheDir, "ask.m4a")
    try {
      recorder = MediaRecorder(getApplication()).apply {
        setAudioSource(MediaRecorder.AudioSource.MIC)
        setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        setAudioSamplingRate(16000)
        setAudioEncodingBitRate(48000)
        setOutputFile(f.absolutePath)
        prepare()
        start()
      }
      askFile = f
      _ui.update { it.copy(asking = true) }
    } catch (e: Exception) {
      Log.w(TAG, "ask: $e")
      recorder = null
    }
  }

  fun stopAsking() {
    val r = recorder ?: return
    recorder = null
    _ui.update { it.copy(asking = false) }
    try { r.stop() } catch (e: Exception) { Log.w(TAG, "ask stop: $e"); r.release(); return }
    r.release()
    val f = askFile ?: return
    val u = _ui.value
    viewModelScope.launch(Dispatchers.IO) {
      val url = u.server.replaceFirst("ws", "http") + "/api/ask?lang=" + u.lang
      val body = f.readBytes().toRequestBody("audio/mp4".toMediaType())
      try {
        http.newCall(Request.Builder().url(url).post(body).build()).execute().use { resp ->
          Log.i(TAG, "ask: ${resp.code} ${resp.body?.string()?.take(200)}")
        }
      } catch (e: Exception) { Log.w(TAG, "ask send: $e") }
    }
  }

  fun stop() {
    _ui.update { it.copy(running = false, status = "Stopped") }
    teardown()
  }

  private fun fail(msg: String) {
    _ui.update { it.copy(running = false, status = msg) }
    teardown()
  }

  private fun teardown() {
    camera?.stop()
    camera = null
    stream = null
    session?.stop()
    session = null
    socket?.close(1000, "stop")
    socket = null
  }

  override fun onCleared() {
    teardown()
    http.dispatcher.executorService.shutdown()
  }

  companion object {
    private const val TAG = "Apprentice"
    // 15 fps per Meta's guidance on the Bluetooth regression (android issue #162); the stream
    // shed to 7 fps / ~55 kbps when 7 was requested
    private const val FRAME_RATE = 15
  }
}
