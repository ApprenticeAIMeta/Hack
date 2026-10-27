package ai.apprentice.glasses

import android.Manifest.permission.BLUETOOTH
import android.Manifest.permission.BLUETOOTH_CONNECT
import android.Manifest.permission.INTERNET
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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

private val Ink = Color(0xFF1C2226)
private val Blue = Color(0xFF1E5AA8)
private val Red = Color(0xFFC2312B)
private val Yellow = Color(0xFFF5C400)
private val Panel = Color(0xFFF4F6F7)

class MainActivity : ComponentActivity() {
  private val vm: GuideViewModel by viewModels()

  private val androidPermissions =
      registerForActivityResult(RequestMultiplePermissions()) { result ->
        if (result.values.all { it }) {
          Wearables.initialize(this)
          vm.onSdkReady()
        } else {
          vm.status("Apprentice needs Bluetooth access to reach the glasses")
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
    super.onCreate(savedInstanceState)
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    setContent {
      MaterialTheme {
        GuideScreen(
            vm,
            onRegister = { Wearables.startRegistration(this) },
            onStart = { vm.start(::requestWearablePermission) },
        )
      }
    }
    androidPermissions.launch(arrayOf(BLUETOOTH, BLUETOOTH_CONNECT, INTERNET))
  }
}

@Composable
private fun GuideScreen(vm: GuideViewModel, onRegister: () -> Unit, onStart: () -> Unit) {
  val ui by vm.ui.collectAsStateWithLifecycle()
  val registered = ui.registration == RegistrationState.REGISTERED
  Column(
      Modifier.fillMaxSize().background(Color(0xFFD5D9DC)).padding(20.dp),
      verticalArrangement = Arrangement.spacedBy(14.dp),
  ) {
    Text("Apprentice", fontSize = 28.sp, fontWeight = FontWeight.SemiBold, color = Ink)
    Text(ui.status, fontSize = 17.sp, color = Ink)

    if (!registered) {
      Button(onClick = onRegister, colors = ButtonDefaults.buttonColors(containerColor = Ink)) {
        Text("Connect glasses in Meta AI")
      }
      return@Column
    }
    Text(if (ui.hasGlasses) "Glasses connected" else "Put on the glasses and open them", color = Ink)

    OutlinedTextField(ui.server, vm::setServer, label = { Text("Server") }, singleLine = true,
        enabled = !ui.running, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(ui.procedure, vm::setProcedure, label = { Text("Procedure") }, singleLine = true,
        enabled = !ui.running, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(ui.run, vm::setRun, label = { Text("Run name, e.g. novice_corners_out") }, singleLine = true,
        enabled = !ui.running, modifier = Modifier.fillMaxWidth())

    Button(
        onClick = { if (ui.running) vm.stop() else onStart() },
        enabled = ui.running || ui.hasGlasses,
        colors = ButtonDefaults.buttonColors(containerColor = if (ui.running) Red else Blue),
        modifier = Modifier.fillMaxWidth().height(56.dp),
    ) { Text(if (ui.running) "Stop guiding" else "Start guiding", fontSize = 18.sp) }

    if (ui.running) Text("${ui.framesSent} frames sent", color = Ink, fontSize = 14.sp)

    Spacer(Modifier.height(8.dp))
    ui.lastSaid?.let { said ->
      val warn = ui.lastKind == "warn"
      Column(
          Modifier.fillMaxWidth()
              .background(if (warn) Yellow else Panel, RoundedCornerShape(14.dp))
              .padding(16.dp),
      ) {
        Text(if (warn) "The expert warns" else "The expert says", fontWeight = FontWeight.SemiBold, color = Ink)
        Text("“$said”", fontSize = 19.sp, color = Ink)
      }
    }
  }
}
