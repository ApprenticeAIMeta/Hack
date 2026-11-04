package ai.apprentice.lens

import android.Manifest.permission.BLUETOOTH
import android.Manifest.permission.BLUETOOTH_CONNECT
import android.Manifest.permission.INTERNET
import android.graphics.Color as AndroidColor
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.meta.wearable.dat.core.Wearables
import kotlin.math.abs
import kotlinx.coroutines.delay

/**
 * What the wearer sees through a Meta Ray-Ban Display: the world (the live glasses camera, from
 * the ApprenticeAI server) with the Display's monocular panel in the right eye. The panel is
 * Meta's own Display renderer (MockDisplayKit's preview), composited with a screen blend, because a
 * waveguide only adds light: black is see-through. Swipes and taps anywhere are Neural Band
 * gestures (thumb swipe moves focus, index pinch selects, long press goes back).
 */
class LensActivity : ComponentActivity() {
  private val vm: LensViewModel by viewModels()

  private val permissions =
      registerForActivityResult(RequestMultiplePermissions()) {
        Wearables.initialize(this)
        vm.start()
      }

  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge(
        statusBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
        navigationBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
    )
    super.onCreate(savedInstanceState)
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    window.decorView.systemUiVisibility =
        View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
    setContent { LensScreen(vm) }
    permissions.launch(arrayOf(BLUETOOTH, BLUETOOTH_CONNECT, INTERNET))
  }
}

private val Mono = FontFamily.Monospace

@Composable
private fun LensScreen(vm: LensViewModel) {
  val ui by vm.ui.collectAsStateWithLifecycle()
  BoxWithConstraints(
      Modifier
          .fillMaxSize()
          .background(Color.Black)
          .pointerInput(Unit) { detectTapGestures(onTap = { vm.band("pinch") }, onLongPress = { vm.band("back") }) }
          .pointerInput(Unit) { thumbSwipes(vm::band) },
  ) {
    // the world
    ui.frame?.let {
      Image(it.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
    } ?: Text("Waiting for the glasses camera", color = Color(0x66FFFFFF), fontFamily = Mono, fontSize = 13.sp,
        modifier = Modifier.align(Alignment.Center))
    // the lens rim darkens the edge of the view a little, as glasses do
    Canvas(Modifier.fillMaxSize()) {
      drawRect(Brush.radialGradient(listOf(Color.Transparent, Color(0x99000000)),
          center = center, radius = size.maxDimension * 0.62f))
    }

    // the Display: right eye, a little right of and below the line of sight
    val side = minOf(maxHeight * 0.66f, maxWidth * 0.42f)
    Box(
        Modifier
            .size(side)
            .align(Alignment.Center)
            .offset(x = maxWidth * 0.17f, y = maxHeight * 0.06f),
    ) {
      vm.displayKit?.let { kit ->
        AndroidView(
            factory = { ctx ->
              FrameLayout(ctx).apply {
                // a waveguide adds light to the world; a screen blend makes the renderer's black see-through
                setLayerType(View.LAYER_TYPE_HARDWARE, Paint().apply { xfermode = PorterDuffXfermode(PorterDuff.Mode.SCREEN) })
                addView(kit.createPreviewView(ctx), ViewGroup.LayoutParams(-1, -1))
              }
            },
            modifier = Modifier.fillMaxSize(),
        )
      }
    }

    Gesture(ui.gesture, ui.gestureAt, Modifier.align(Alignment.BottomCenter).padding(bottom = 18.dp))
    Text("Simulated Meta Ray-Ban Display", color = Color(0x80FFFFFF), fontFamily = Mono, fontSize = 11.sp,
        modifier = Modifier.align(Alignment.TopStart).padding(16.dp))
    if (ui.status.isNotEmpty())
      Text(ui.status, color = Color(0xB3FFFFFF), fontFamily = Mono, fontSize = 12.sp,
          modifier = Modifier.align(Alignment.TopEnd).padding(16.dp))
  }
}

/** Thumb swipes in any direction, sent as Neural Band navigation (taps are pinches, see above). */
private suspend fun androidx.compose.ui.input.pointer.PointerInputScope.thumbSwipes(send: (String) -> Unit) {
  var total = Offset.Zero
  detectDragGestures(
      onDragStart = { total = Offset.Zero },
      onDrag = { c, d -> c.consume(); total += d },
      onDragEnd = {
        if (maxOf(abs(total.x), abs(total.y)) > 40)
          send(if (abs(total.x) >= abs(total.y)) (if (total.x > 0) "right" else "left") else (if (total.y > 0) "down" else "up"))
      },
  )
}

/** The wristband and the last gesture, so a viewer can see what the hand did. */
@Composable
private fun Gesture(gesture: String?, at: Long, modifier: Modifier) {
  var shown by remember { mutableStateOf(false) }
  LaunchedEffect(at) {
    if (gesture != null) { shown = true; delay(1100); shown = false }
  }
  Row(
      modifier
          .clip(RoundedCornerShape(50))
          .background(Color(0x99000000))
          .border(1.dp, Color(0x26FFFFFF), RoundedCornerShape(50))
          .padding(horizontal = 14.dp, vertical = 8.dp),
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    Box(Modifier.size(7.dp).clip(CircleShape).background(if (shown) Color(0xFF7DD3F5) else Color(0x59FFFFFF)))
    Text("Neural Band", color = Color(0xB3FFFFFF), fontFamily = Mono, fontSize = 12.sp)
    AnimatedVisibility(shown, enter = fadeIn(), exit = fadeOut()) {
      Row {
        Spacer(Modifier.width(2.dp))
        Text(
            when (gesture) {
              "right" -> "thumb swipe →"
              "left" -> "← thumb swipe"
              "up" -> "thumb swipe ↑"
              "down" -> "thumb swipe ↓"
              "pinch" -> "pinch"
              "back" -> "middle pinch: back"
              else -> ""
            },
            color = Color.White, fontFamily = Mono, fontSize = 12.sp,
        )
      }
    }
  }
}
