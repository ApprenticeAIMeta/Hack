package ai.apprentice.lens

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.display.Display
import com.meta.wearable.dat.display.addDisplay
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.display.views.ActionRole
import com.meta.wearable.dat.display.views.Alignment
import com.meta.wearable.dat.display.views.ButtonStyle
import com.meta.wearable.dat.display.views.CornerRadius
import com.meta.wearable.dat.display.views.Direction
import com.meta.wearable.dat.display.views.FlexBoxBackground
import com.meta.wearable.dat.display.views.IconName
import com.meta.wearable.dat.display.views.ImageSize
import com.meta.wearable.dat.display.views.TextColor
import com.meta.wearable.dat.display.views.TextStyle
import com.meta.wearable.dat.inputs.types.InputSource
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import com.meta.wearable.dat.mockdevice.api.MockDeviceKitConfig
import com.meta.wearable.dat.mockdevice.api.MockGlasses
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject

/** What the lens is showing; every change is re-sent to the Display as one screen. */
data class Lens(
    val procedure: String = "airplane",
    val lang: String = "en",
    val title: String = "",
    val steps: Int = 0,
    val step: Int? = null,
    val finished: Boolean = false,
    val note: Note? = null,
    val waiting: String = "Waiting for the glasses",
)

/** One line above the step: a check in progress, its verdict, a warning or the expert's answer. */
data class Note(val text: String, val icon: IconName, val strong: Boolean = false)

data class LensUi(
    val frame: Bitmap? = null,
    val ready: Boolean = false,
    val gesture: String? = null,
    val gestureAt: Long = 0,
    val status: String = "Starting the display",
)

/**
 * The guide on a Meta Ray-Ban Display, simulated: Meta's MockDeviceKit pairs a virtual Ray-Ban
 * Display, and the screens below go through the real Display API (`sendContent`), so the lens
 * shows exactly what the glasses would render. The step comes from the ApprenticeAI server's
 * guide socket; the lens buttons call the same endpoints as the phone guide. Neural Band gestures
 * are injected through MockInputKit with InputSource.NEURAL_BAND.
 */
class LensViewModel(app: Application) : AndroidViewModel(app) {
  private val _ui = MutableStateFlow(LensUi())
  val ui: StateFlow<LensUi> = _ui
  private val lens = MutableStateFlow(Lens())

  private val http = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()
  private val server = BuildConfig.DEFAULT_SERVER
  private val base = server.replaceFirst("ws", "http")
  private var socket: WebSocket? = null
  private var glasses: MockGlasses? = null
  private var session: DeviceSession? = null
  private var display: Display? = null
  val displayKit get() = glasses?.services?.display

  private var proc: JSONObject? = null
  private var procKey = ""
  private val photos = mutableMapOf<String, Bitmap?>()
  private var noteJob: Job? = null
  private var checking = false
  private var lastSent = ""

  fun start() {
    if (glasses != null) return
    val kit = MockDeviceKit.getInstance(getApplication())
    kit.enable(MockDeviceKitConfig(true, true))
    kit.pairGlasses(GlassesModel.META_RAYBAN_DISPLAY)
        .onSuccess { g ->
          g.powerOn()
          g.unfold()
          g.don()
          glasses = g
          openDisplay()
        }
        .onFailure { e, _ -> status("Could not start the simulated Display: $e") }
    connect()
    viewModelScope.launch { lens.collect { render(it) } }
  }

  private fun openDisplay() {
    Wearables.createSession(AutoDeviceSelector(filter = { it.isDisplayCapable() }))
        .onSuccess { s ->
          session = s
          viewModelScope.launch { s.errors.collect { Log.w(TAG, "session: ${it.description}") } }
          s.start()
          viewModelScope.launch {
            s.state.first { it == DeviceSessionState.STARTED }
            s.addDisplay()
                .onSuccess { d ->
                  display = d
                  viewModelScope.launch {
                    d.state.collect { st ->
                      val ready = st == DisplayState.STARTED
                      _ui.update { it.copy(ready = ready, status = if (ready) "" else "Display $st") }
                      if (ready) { lastSent = ""; render(lens.value) }
                    }
                  }
                }
                .onFailure { e, _ -> status("Display unavailable: ${e.description}") }
          }
        }
        .onFailure { e, _ -> status("No Display glasses: ${e.description}") }
  }

  // -- Neural Band ------------------------------------------------------------------------------

  fun band(gesture: String) {
    val input = glasses?.services?.input ?: return
    when (gesture) {
      "right" -> input.navRight(InputSource.NEURAL_BAND)
      "left" -> input.navLeft(InputSource.NEURAL_BAND)
      "up" -> input.navUp(InputSource.NEURAL_BAND)
      "down" -> input.navDown(InputSource.NEURAL_BAND)
      "pinch" -> input.select(InputSource.NEURAL_BAND)
      "back" -> input.back(InputSource.NEURAL_BAND)
    }
    _ui.update { it.copy(gesture = gesture, gestureAt = System.currentTimeMillis()) }
  }

  // -- the lens screens -------------------------------------------------------------------------

  private suspend fun render(l: Lens) {
    val d = display ?: return
    if (!_ui.value.ready) return
    val key = l.toString()
    if (key == lastSent) return
    lastSent = key
    val k = l.step
    val s = if (k != null) proc?.optJSONArray("steps")?.optJSONObject(k) else null
    val photo = if (s != null && !l.finished) photo(s) else null
    d.sendContent {
      flexBox(direction = Direction.COLUMN, gap = 12) {
        when {
          l.finished -> flexBox(padding = 24, gap = 10, background = FlexBoxBackground.CARD, crossAlignment = Alignment.CENTER) {
            icon(IconName.CHECKMARK_CIRCLE)
            text("All ${l.steps} steps done", style = TextStyle.HEADING)
            text(l.title, style = TextStyle.META, color = TextColor.SECONDARY)
          }
          s == null || k == null -> flexBox(padding = 24, gap = 8, background = FlexBoxBackground.CARD) {
            text("Apprentice", style = TextStyle.META, color = TextColor.SECONDARY)
            text(l.waiting, style = TextStyle.BODY)
          }
          else -> {
            l.note?.let { n ->
              flexBox(direction = Direction.ROW, gap = 8, crossAlignment = Alignment.CENTER, padding = 12,
                  background = if (n.strong) FlexBoxBackground.CARD else FlexBoxBackground.NONE) {
                icon(n.icon)
                text(n.text, style = TextStyle.BODY, color = if (n.strong) TextColor.PRIMARY else TextColor.SECONDARY)
              }
            }
            flexBox(padding = 20, gap = 6, background = FlexBoxBackground.CARD) {
              text("Step ${k + 1} of ${l.steps}", style = TextStyle.META, color = TextColor.SECONDARY)
              text(s.optString("title"), style = TextStyle.HEADING)
              text(s.optString("instruction"), style = TextStyle.BODY, color = TextColor.SECONDARY)
            }
            if (photo != null) {
              flexBox(gap = 6) {
                text("Expert's result", style = TextStyle.META, color = TextColor.SECONDARY)
                image(bitmap = photo, sizePreset = ImageSize.FILL, cornerRadius = CornerRadius.MEDIUM)
              }
            }
            flexBox(direction = Direction.ROW, alignment = Alignment.CENTER, crossAlignment = Alignment.CENTER) {
              buttonGroup {
                button("Check me", style = ButtonStyle.PRIMARY, iconName = IconName.EYE,
                    onClick = { checkMe() }, actionRole = ActionRole.PRIMARY)
                if (k + 1 < l.steps)
                  button("Next step", style = ButtonStyle.SECONDARY, iconName = IconName.ARROW_RIGHT,
                      onClick = { post("/api/goto?step=${k + 1}") })
              }
            }
          }
        }
      }
    }.onFailure { e, _ -> Log.w(TAG, "send: ${e.description}") }
  }

  private fun checkMe() {
    if (checking) return
    checking = true
    note(Note("Checking your work", IconName.EYE), 20_000) { checking = false }
    post("/api/check")
  }

  private fun post(path: String) {
    viewModelScope.launch(Dispatchers.IO) {
      try {
        http.newCall(Request.Builder().url(base + path).post(ByteArray(0).toRequestBody()).build()).execute().use {
          if (!it.isSuccessful) Log.w(TAG, "$path: ${it.code}")
        }
      } catch (e: Exception) { Log.w(TAG, "$path: $e") }
    }
  }

  private fun note(n: Note?, ms: Long = 8000, after: () -> Unit = {}) {
    noteJob?.cancel()
    lens.update { it.copy(note = n) }
    if (n != null) noteJob = viewModelScope.launch {
      delay(ms)
      lens.update { it.copy(note = null) }
      after()
    }
  }

  private suspend fun photo(step: JSONObject): Bitmap? {
    val rf = step.optJSONObject("reference_frame") ?: return null
    val f = rf.optString("crop").ifEmpty { rf.optString("file") }.ifEmpty { return null }
    val url = "$base/procedures/${lens.value.procedure}/$f"
    return photos.getOrPut(url) {
      withContext(Dispatchers.IO) {
        try {
          http.newCall(Request.Builder().url(url).build()).execute().use { r ->
            r.body?.bytes()?.let { b ->
              val full = BitmapFactory.decodeByteArray(b, 0, b.size) ?: return@let null
              val side = 280                                   // the lens is 600 px across
              val h = (full.height * side / full.width.toFloat()).toInt().coerceAtMost(side)
              Bitmap.createScaledBitmap(full, side, h, true)
            }
          }
        } catch (e: Exception) { Log.w(TAG, "photo: $e"); null }
      }
    }
  }

  private suspend fun loadProcedure(name: String, lang: String) {
    val key = "$name/$lang"
    if (key == procKey) return
    val p = withContext(Dispatchers.IO) {
      try {
        http.newCall(Request.Builder().url("$base/api/procedure/$name?lang=$lang").build()).execute().use { r ->
          r.body?.string()?.let { JSONObject(it) }
        }
      } catch (e: Exception) { Log.w(TAG, "procedure: $e"); null }
    } ?: return
    proc = p
    procKey = key
    photos.clear()
    lens.update { it.copy(procedure = name, lang = lang, title = p.optString("task"), steps = p.optJSONArray("steps")?.length() ?: 0) }
  }

  // -- the guide socket -------------------------------------------------------------------------

  private fun connect() {
    viewModelScope.launch { loadProcedure(lens.value.procedure, lens.value.lang) }
    socket = http.newWebSocket(Request.Builder().url("$server/ws/guide").build(), object : WebSocketListener() {
      override fun onMessage(webSocket: WebSocket, text: String) {
        val m = JSONObject(text)
        viewModelScope.launch { onGuide(m) }
      }

      override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        lens.update { it.copy(step = null, waiting = "Can't reach the Apprentice server") }
        viewModelScope.launch { delay(1500); connect() }
      }
    })
  }

  private suspend fun onGuide(m: JSONObject) {
    when (m.optString("type")) {
      "frame" -> {
        val b = Base64.decode(m.getString("jpeg"), Base64.DEFAULT)
        val bmp = withContext(Dispatchers.Default) { BitmapFactory.decodeByteArray(b, 0, b.size) }
        if (bmp != null) _ui.update { it.copy(frame = bmp) }
      }
      "session" ->
        if (m.optString("status") == "started") {
          loadProcedure(m.optString("procedure", lens.value.procedure), m.optString("lang", "en").ifEmpty { "en" })
          checking = false
          note(null)
          lens.update { it.copy(step = null, finished = false, waiting = "Starting") }
        } else lens.update { it.copy(step = null, waiting = "Session ended") }
      "settled" -> if (!checking) { checking = true; note(Note("Hands off. Checking your work", IconName.EYE), 20_000) { checking = false } }
      "ask" -> {
        val clip = m.optJSONObject("clip")
        val said = m.optString("answer")
        note(Note(if (clip != null) "Expert: \"${clip.optString("text")}\"" else said, IconName.SPEECH_BUBBLE, strong = true), 12_000)
      }
      "state" -> {
        val k = m.optInt("step")
        if (k != lens.value.step && lens.value.step != null) { checking = false; note(null) }
        lens.update { it.copy(step = k, finished = m.optBoolean("finished")) }
        val evs = m.optJSONArray("events") ?: return
        for (i in 0 until evs.length()) {
          val ev = evs.getJSONObject(i)
          when (ev.optString("kind")) {
            "warn" -> note(Note(ev.optJSONObject("data")?.optString("what")?.ifEmpty { null } ?: "Check this step again",
                IconName.EXCLAMATION_TRIANGLE, strong = true), 10_000)
            "step_done" -> note(Note("Step ${ev.optInt("step") + 1} done", IconName.CHECKMARK), 4000)
            "check" -> if (ev.optString("check") == "done" && (checking || ev.has("trigger") && !ev.isNull("trigger"))) {
              checking = false
              val ahead = !ev.isNull("completed") && ev.optInt("completed", -1) > ev.optInt("step")
              note(if (ahead) Note("Looks done. Confirming", IconName.CHECKMARK)
                   else Note(if (ev.optBoolean("unclear")) "Look down at the work and try again" else ev.optString("reason").ifEmpty { "Not finished yet" },
                       IconName.I_CIRCLE, strong = true), 8000)
            }
          }
        }
      }
    }
  }

  private fun status(s: String) = _ui.update { it.copy(status = s) }

  override fun onCleared() {
    socket?.close(1000, "bye")
    display?.stop()
    session?.stop()
    MockDeviceKit.getInstance(getApplication()).disable()
    http.dispatcher.executorService.shutdown()
  }

  companion object { private const val TAG = "ApprenticeLens" }
}
