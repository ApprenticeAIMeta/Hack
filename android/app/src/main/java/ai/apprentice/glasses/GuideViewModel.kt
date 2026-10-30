package ai.apprentice.glasses

import android.app.Application
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.addCamera
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
import java.util.ArrayDeque
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
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

  private val selector = AutoDeviceSelector()
  private val http = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()
  private var socket: WebSocket? = null
  private var session: DeviceSession? = null
  private var camera: Camera? = null
  private val clips = ArrayDeque<String>()
  private var player: MediaPlayer? = null

  fun onSdkReady() {
    viewModelScope.launch { Wearables.registrationState.collect { r -> _ui.update { it.copy(registration = r) } } }
    viewModelScope.launch { selector.activeDeviceFlow().collect { d -> _ui.update { it.copy(hasGlasses = d != null) } } }
  }

  fun setServer(s: String) = _ui.update { it.copy(server = s.trim()) }

  fun setProcedure(p: String) = _ui.update { it.copy(procedure = p.trim()) }

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
          .put("width", frame.width).put("height", frame.height).put("fps", FRAME_RATE).toString())
    }
    ws.send(bytes.toByteString())
    _ui.update { it.copy(framesSent = it.framesSent + 1) }
  }

  private fun openSocket() {
    val u = _ui.value
    // The server records every session under this run name (out/sessions/<time>_<run>)
    val run = java.net.URLEncoder.encode(u.run.trim().ifEmpty { "run" }, "UTF-8")
    val req = Request.Builder().url("${u.server}/ws/device?procedure=${u.procedure}&run=$run").build()
    socket = http.newWebSocket(req, object : WebSocketListener() {
      override fun onMessage(webSocket: WebSocket, text: String) {
        val m = JSONObject(text)
        if (m.optString("type") == "play") {
          enqueue(u.server.replaceFirst("ws", "http") + m.getString("url"))
        }
      }

      override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        if (_ui.value.running) fail("Server connection lost: ${t.message}")
      }
    })
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
