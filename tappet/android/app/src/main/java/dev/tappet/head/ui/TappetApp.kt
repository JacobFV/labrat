package dev.tappet.head.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.tappet.head.body.LinkState
import dev.tappet.head.service.HeadController
import dev.tappet.head.service.HeadService
import kotlinx.coroutines.launch

enum class Screen { FACE, SETTINGS, CALIBRATION, MANUAL }

val Accent = Color(0xFF7FE7FF)

@Composable
fun TappetApp() {
    MaterialTheme(colorScheme = darkColorScheme(primary = Accent, secondary = Color(0xFFB79CFF))) {
        val ctrl by HeadService.controller.collectAsState()
        val c = ctrl
        if (c == null) {
            Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@MaterialTheme
        }
        var screen by rememberSaveable { mutableStateOf(Screen.FACE) }
        var overlay by rememberSaveable { mutableStateOf(false) }
        BackHandler(enabled = screen != Screen.FACE || overlay) {
            if (screen != Screen.FACE) screen = Screen.FACE else overlay = false
        }
        when (screen) {
            Screen.FACE -> FaceScreen(c, overlay, onOverlay = { overlay = it }, onNavigate = { screen = it; overlay = false })
            Screen.SETTINGS -> SettingsScreen(c) { screen = Screen.FACE }
            Screen.CALIBRATION -> CalibrationScreen(c) { screen = Screen.FACE }
            Screen.MANUAL -> ManualScreen(c) { screen = Screen.FACE }
        }
    }
}

@Composable
private fun FaceScreen(c: HeadController, overlay: Boolean, onOverlay: (Boolean) -> Unit, onNavigate: (Screen) -> Unit) {
    val face by c.face.collectAsState()
    val pin by c.pairing.pin.collectAsState()
    val link by c.client.status.collectAsState()
    val reply by c.lastReply.collectAsState()
    val speaking by c.speaker.speaking.collectAsState()
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        RobotFace(
            face,
            Modifier.fillMaxSize().clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) { onOverlay(true) },
        )
        // status line: link dot + pairing info
        Row(
            Modifier.align(Alignment.BottomCenter).padding(bottom = 18.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(10.dp).background(if (link.connected) Color(0xFF4ADE80) else Color(0xFFF87171), CircleShape))
            Spacer(Modifier.width(8.dp))
            Text("PIN $pin  ·  ${c.webUrl().removePrefix("http://")}", color = Color(0x99FFFFFF), fontSize = 14.sp, fontFamily = FontFamily.Monospace)
        }
        if (speaking && reply.isNotBlank()) {
            Text(
                reply, color = Color(0xCCFFFFFF), fontSize = 18.sp,
                modifier = Modifier.align(Alignment.TopCenter).padding(top = 24.dp, start = 32.dp, end = 32.dp),
                maxLines = 3,
            )
        }
        if (overlay) Overlay(c, onClose = { onOverlay(false) }, onNavigate = onNavigate)
    }
}

@Composable
private fun Overlay(c: HeadController, onClose: () -> Unit, onNavigate: (Screen) -> Unit) {
    val transcript by c.agent.transcript.collectAsState()
    val busy by c.agent.busy.collectAsState()
    val link by c.client.status.collectAsState()
    val pin by c.pairing.pin.collectAsState()
    val partial by c.voice.partial.collectAsState()
    val mode by c.voice.mode.collectAsState()
    val webErr by c.webError.collectAsState()
    val scope = rememberCoroutineScope()
    var input by rememberSaveable { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val url = c.webUrl()
    val qr = remember(url) { qrBitmap(url) }
    val listState = rememberLazyListState()
    LaunchedEffect(transcript.size) { if (transcript.isNotEmpty()) listState.animateScrollToItem(transcript.size - 1) }

    Surface(Modifier.fillMaxSize(), color = Color(0xF0101418)) {
        Column(Modifier.fillMaxSize().safeDrawingPadding().padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Tappet", style = MaterialTheme.typography.headlineSmall, color = Accent)
                    val linkText = when (link.state) {
                        LinkState.CONNECTED -> "Body connected · ${link.transport}"
                        LinkState.CONNECTING -> "Connecting · ${link.transport ?: ""}"
                        LinkState.DISCONNECTED -> "Body offline${link.detail?.let { " · $it" } ?: ""}"
                    }
                    Text(linkText, fontSize = 13.sp, color = if (link.connected) Color(0xFF4ADE80) else Color(0xFFF87171))
                    Text("Remote: $url", fontSize = 13.sp)
                    Text("Pairing PIN: $pin", fontSize = 20.sp, fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace)
                    webErr?.let { Text(it, color = Color(0xFFF87171), fontSize = 12.sp) }
                    Text("Voice: ${mode.name.lowercase()}${if (partial.isNotBlank()) " · \"$partial\"" else ""}", fontSize = 12.sp, color = Color.Gray)
                }
                Image(qr, contentDescription = "QR code for $url", modifier = Modifier.size(120.dp).background(Color.White, RoundedCornerShape(6.dp)).padding(4.dp))
            }
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton(onClick = { onNavigate(Screen.SETTINGS) }) { Text("Settings") }
                OutlinedButton(onClick = { onNavigate(Screen.CALIBRATION) }) { Text("Calibrate") }
                OutlinedButton(onClick = { onNavigate(Screen.MANUAL) }) { Text("Manual") }
                Spacer(Modifier.weight(1f))
                TextButton(onClick = onClose) { Text("Close") }
            }
            LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = listState) {
                items(transcript) { e ->
                    val color = when (e.role) {
                        "user" -> Color.White
                        "assistant" -> Accent
                        "error" -> Color(0xFFF87171)
                        else -> Color.Gray
                    }
                    val prefix = when (e.role) { "user" -> "You: "; "assistant" -> "Tappet: "; "tool" -> "  ⚙ "; else -> "" }
                    Text(prefix + e.text, color = color, fontSize = if (e.role == "tool") 12.sp else 15.sp, modifier = Modifier.padding(vertical = 2.dp))
                }
                if (busy) item { Text("Thinking…", color = Color.Gray) }
            }
            error?.let { Text(it, color = Color(0xFFF87171), fontSize = 12.sp) }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = input, onValueChange = { input = it }, modifier = Modifier.weight(1f),
                    placeholder = { Text(if (c.settings.value.apiKey.isBlank()) "Set an API key in Settings first" else "Ask Tappet…") },
                    singleLine = true,
                )
                Spacer(Modifier.width(6.dp))
                Button(enabled = !busy && input.isNotBlank(), onClick = {
                    val text = input.trim()
                    input = ""
                    error = null
                    scope.launch {
                        try { c.chat(text, speak = true) } catch (e: Exception) { error = e.message }
                    }
                }) { Text("Send") }
                Spacer(Modifier.width(6.dp))
                Button(enabled = !busy, onClick = { c.voice.pushToTalk() }) { Text("Talk") }
            }
            Spacer(Modifier.height(4.dp))
        }
    }
}
