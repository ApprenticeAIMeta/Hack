package ai.apprentice.glasses

import android.Manifest.permission.BLUETOOTH
import android.Manifest.permission.BLUETOOTH_CONNECT
import android.Manifest.permission.INTERNET
import android.Manifest.permission.RECORD_AUDIO
import android.annotation.SuppressLint
import android.graphics.Color as AndroidColor
import android.os.Bundle
import android.view.WindowManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import androidx.activity.viewModels
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.meta.wearable.dat.core.types.RegistrationState
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

// The same heads-up look as the dashboard and the guide page: near-black, glass panels with a
// hairline border, cyan for live state, amber for attention, monospace labels.
private val Bg = Color(0xFF030507)
private val Glass = Color(0xFF0C0F13)
private val Line = Color(0x1AFFFFFF)
private val Dim = Color(0x8CFFFFFF)
private val Faint = Color(0x59FFFFFF)
private val Cyan = Color(0xFF67E8F9)
private val CyanSoft = Color(0x2667E8F9)
private val Amber = Color(0xFFFBBF24)
private val Rose = Color(0xFFF43F5E)
private val Mono = FontFamily.Monospace

private val LANGS = listOf("en" to "English", "hi" to "Hindi", "es" to "Spanish")

class MainActivity : ComponentActivity() {
  private val vm: GuideViewModel by viewModels()

  private val androidPermissions =
      registerForActivityResult(RequestMultiplePermissions()) { result ->
        if (result.values.all { it }) {
          Wearables.initialize(this)
          vm.onSdkReady()
        } else {
          vm.status("Apprentice needs Bluetooth and microphone access")
        }
      }

  // Camera access on the glasses is granted in the Meta AI app; this suspends until it answers.
  private val permissionMutex = Mutex()
  private var permissionContinuation: CancellableContinuation<PermissionStatus>? = null
  private val wearablePermission =
      registerForActivityResult(Wearables.RequestPermissionContract()) { result ->
        permissionContinuation?.resume(result.getOrDefault(PermissionStatus.Denied))
        permissionContinuation = null
      }

  private suspend fun requestWearablePermission(p: Permission): PermissionStatus =
      permissionMutex.withLock {
        suspendCancellableCoroutine { c ->
          permissionContinuation = c
          c.invokeOnCancellation { permissionContinuation = null }
          wearablePermission.launch(p)
        }
      }

  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge(
        statusBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
        navigationBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
    )
    super.onCreate(savedInstanceState)
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    setContent {
      Root(
          vm,
          onRegister = { Wearables.startRegistration(this) },
          onStart = { vm.start(::requestWearablePermission) },
      )
    }
    androidPermissions.launch(arrayOf(BLUETOOTH, BLUETOOTH_CONNECT, INTERNET, RECORD_AUDIO))
  }
}

@Composable
private fun Root(vm: GuideViewModel, onRegister: () -> Unit, onStart: () -> Unit) {
  val ui by vm.ui.collectAsStateWithLifecycle()
  if (ui.running) GuidingScreen(vm, ui) else SetupScreen(vm, ui, onRegister, onStart)
}

// -- setup ------------------------------------------------------------------------------------

@Composable
private fun SetupScreen(vm: GuideViewModel, ui: GuideUi, onRegister: () -> Unit, onStart: () -> Unit) {
  val registered = ui.registration == RegistrationState.REGISTERED
  Column(
      Modifier
          .fillMaxSize()
          .background(Brush.radialGradient(listOf(Color(0xFF0B1A22), Bg), radius = 1400f))
          .systemBarsPadding()
          .verticalScroll(rememberScrollState())
          .padding(horizontal = 20.dp, vertical = 16.dp),
      verticalArrangement = Arrangement.spacedBy(14.dp),
  ) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp, bottom = 6.dp)) {
      Box(
          Modifier.size(30.dp).clip(RoundedCornerShape(9.dp)).background(CyanSoft).border(1.dp, Cyan.copy(alpha = .5f), RoundedCornerShape(9.dp)),
          contentAlignment = Alignment.Center,
      ) { Box(Modifier.size(9.dp).clip(CircleShape).background(Cyan)) }
      Spacer(Modifier.width(12.dp))
      Text("ApprenticeAI", color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
    }

    Panel {
      Label("GLASSES")
      Row(verticalAlignment = Alignment.CenterVertically) {
        LiveDot(if (ui.hasGlasses) Cyan else if (registered) Amber else Faint, pulse = ui.hasGlasses)
        Spacer(Modifier.width(10.dp))
        Text(
            when {
              !registered -> "Not connected to Apprentice"
              ui.hasGlasses -> "Connected and ready"
              else -> "Put on the glasses and unfold them"
            },
            color = Color.White, fontSize = 17.sp,
        )
      }
      if (ui.status.isNotBlank()) Text(ui.status, color = Dim, fontSize = 14.sp, fontFamily = Mono)
      if (!registered) PrimaryButton("Connect glasses in Meta AI", onClick = onRegister)
    }

    if (registered) {
      Panel {
        Label("SESSION")
        Field("PROCEDURE", ui.procedure, vm::setProcedure)
        Field("RUN NAME", ui.run, vm::setRun, placeholder = "e.g. catch2")
        Label("LANGUAGE")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
          LANGS.forEach { (code, name) -> Chip(name, selected = ui.lang == code) { vm.setLang(code) } }
        }
      }

      Panel {
        Label("SERVER")
        Field("ADDRESS", ui.server, vm::setServer)
        Text("Over the USB cable this stays ws://localhost:8000", color = Faint, fontSize = 12.sp, fontFamily = Mono)
      }

      PrimaryButton(
          if (ui.hasGlasses) "Start guiding" else "Waiting for the glasses",
          enabled = ui.hasGlasses, tall = true, onClick = onStart,
      )
      Text(
          "Done with a step? Set the paper down and take your hands off for 2 seconds.",
          color = Faint, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 4.dp),
      )
    }
  }
}

// -- guiding ----------------------------------------------------------------------------------

/** The server's guide page is the novice's screen; the expert's voice plays alongside it. */
@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun GuidingScreen(vm: GuideViewModel, ui: GuideUi) {
  val url = ui.server.replaceFirst("ws", "http") + "/guide?procedure=" + ui.procedure + "&lang=" + ui.lang
  Column(Modifier.fillMaxSize().background(Bg).systemBarsPadding()) {
    AndroidView(
        factory = { context ->
          WebView(context).apply {
            setBackgroundColor(AndroidColor.parseColor("#030507"))
            settings.javaScriptEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            webViewClient = WebViewClient()
            loadUrl(url)
          }
        },
        modifier = Modifier.weight(1f).fillMaxWidth(),
    )
    Row(
        Modifier
            .fillMaxWidth()
            .background(Glass)
            .border(BorderStroke(1.dp, Line))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
      Column(Modifier.weight(1f)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
          LiveDot(Rose, pulse = true)
          Spacer(Modifier.width(8.dp))
          Text("LIVE", color = Color.White, fontSize = 12.sp, fontFamily = Mono, letterSpacing = 2.sp)
        }
        Text("${ui.framesSent} frames", color = Faint, fontSize = 11.sp, fontFamily = Mono)
      }
      AskButton(ui.asking, vm::startAsking, vm::stopAsking)
      Spacer(Modifier.width(8.dp))
      OutlineButton("Stop", Rose, onClick = vm::stop)
    }
  }
}

/** Hold to record a question for the expert on the phone's mic; release to send it. */
@Composable
private fun AskButton(asking: Boolean, onStart: () -> Unit, onStop: () -> Unit) {
  val bg by animateColorAsState(if (asking) Amber else CyanSoft, label = "ask")
  Box(
      Modifier
          .clip(RoundedCornerShape(14.dp))
          .background(bg)
          .border(1.dp, if (asking) Amber else Cyan.copy(alpha = .5f), RoundedCornerShape(14.dp))
          .pointerInput(Unit) {
            detectTapGestures(onPress = {
              onStart()
              try { awaitRelease() } finally { onStop() }
            })
          }
          .padding(horizontal = 16.dp, vertical = 12.dp),
  ) {
    Text(
        if (asking) "LISTENING…" else "HOLD TO ASK",
        color = if (asking) Color.Black else Cyan, fontSize = 13.sp, fontFamily = Mono,
        fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp,
    )
  }
}

// -- pieces -----------------------------------------------------------------------------------

@Composable
private fun Panel(content: @Composable () -> Unit) {
  Column(
      Modifier
          .fillMaxWidth()
          .clip(RoundedCornerShape(18.dp))
          .background(Glass.copy(alpha = .85f))
          .border(1.dp, Line, RoundedCornerShape(18.dp))
          .padding(16.dp),
      verticalArrangement = Arrangement.spacedBy(10.dp),
  ) { content() }
}

@Composable
private fun Label(text: String) =
    Text(text, color = Faint, fontSize = 11.sp, fontFamily = Mono, letterSpacing = 2.5.sp)

@Composable
private fun LiveDot(color: Color, pulse: Boolean = false) {
  val a by rememberInfiniteTransition(label = "dot").animateFloat(
      1f, if (pulse) .3f else 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
  Box(Modifier.size(8.dp).alpha(a).clip(CircleShape).background(color))
}

@Composable
private fun Field(label: String, value: String, onChange: (String) -> Unit, placeholder: String = "") {
  Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
    Text(label, color = Faint, fontSize = 10.sp, fontFamily = Mono, letterSpacing = 1.5.sp)
    BasicTextField(
        value = value, onValueChange = onChange, singleLine = true,
        textStyle = TextStyle(color = Color.White, fontSize = 16.sp, fontFamily = Mono),
        cursorBrush = SolidColor(Cyan),
        decorationBox = { inner ->
          Box(
              Modifier
                  .fillMaxWidth()
                  .clip(RoundedCornerShape(12.dp))
                  .background(Color(0x0DFFFFFF))
                  .border(1.dp, Line, RoundedCornerShape(12.dp))
                  .padding(horizontal = 14.dp, vertical = 12.dp),
          ) {
            if (value.isEmpty()) Text(placeholder, color = Faint, fontSize = 16.sp, fontFamily = Mono)
            inner()
          }
        },
    )
  }
}

@Composable
private fun Chip(text: String, selected: Boolean, onClick: () -> Unit) {
  Box(
      Modifier
          .clip(RoundedCornerShape(10.dp))
          .background(if (selected) CyanSoft else Color(0x0DFFFFFF))
          .border(1.dp, if (selected) Cyan.copy(alpha = .6f) else Line, RoundedCornerShape(10.dp))
          .clickable(onClick = onClick)
          .padding(horizontal = 14.dp, vertical = 9.dp),
  ) { Text(text, color = if (selected) Cyan else Dim, fontSize = 14.sp) }
}

@Composable
private fun PrimaryButton(text: String, enabled: Boolean = true, tall: Boolean = false, onClick: () -> Unit) {
  Box(
      Modifier
          .fillMaxWidth()
          .height(if (tall) 58.dp else 48.dp)
          .clip(RoundedCornerShape(16.dp))
          .background(if (enabled) Cyan else Color(0x14FFFFFF))
          .clickable(enabled = enabled, onClick = onClick),
      contentAlignment = Alignment.Center,
  ) {
    Text(text, color = if (enabled) Color.Black else Faint, fontSize = if (tall) 18.sp else 16.sp, fontWeight = FontWeight.SemiBold)
  }
}

@Composable
private fun OutlineButton(text: String, color: Color, onClick: () -> Unit) {
  Box(
      Modifier
          .clip(RoundedCornerShape(14.dp))
          .border(1.dp, color.copy(alpha = .6f), RoundedCornerShape(14.dp))
          .clickable(onClick = onClick)
          .padding(horizontal = 16.dp, vertical = 12.dp),
  ) { Text(text.uppercase(), color = color, fontSize = 13.sp, fontFamily = Mono, letterSpacing = 1.sp, fontWeight = FontWeight.SemiBold) }
}
