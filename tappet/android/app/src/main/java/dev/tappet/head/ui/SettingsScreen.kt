package dev.tappet.head.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import dev.tappet.head.config.KeyboardParams
import dev.tappet.head.config.NeckPose
import dev.tappet.head.config.TransportChoice
import dev.tappet.head.service.HeadController

@Composable
fun ScreenScaffold(title: String, onBack: () -> Unit, content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().background(Color(0xFF101418))) {
        Column(Modifier.fillMaxSize().safeDrawingPadding().padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack) { Text("‹ Back") }
                Text(title, style = MaterialTheme.typography.titleLarge, color = Accent)
            }
            content()
        }
    }
}

@Composable
private fun Section(title: String) {
    HorizontalDivider(Modifier.padding(vertical = 8.dp))
    Text(title, style = MaterialTheme.typography.titleMedium)
}

@Composable
private fun NumField(label: String, value: String, onChange: (String) -> Unit, modifier: Modifier = Modifier) {
    OutlinedTextField(
        value, onChange, label = { Text(label) }, singleLine = true, modifier = modifier,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
    )
}

@Composable
private fun Toggle(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, Modifier.weight(1f))
        Switch(checked, onChange)
    }
}

@Composable
fun SettingsScreen(c: HeadController, onBack: () -> Unit) {
    val s0 = c.settings.value
    var apiKey by remember { mutableStateOf(s0.apiKey) }
    var model by remember { mutableStateOf(s0.model) }
    var transport by remember { mutableStateOf(s0.transport) }
    var simHost by remember { mutableStateOf(s0.simHost) }
    var simPort by remember { mutableStateOf(s0.simPort.toString()) }
    var profile by remember { mutableStateOf(c.currentProfile().name) }
    var kbTop by remember { mutableStateOf(s0.keyboard.top.toString()) }
    var kbBottom by remember { mutableStateOf(s0.keyboard.bottom.toString()) }
    var kbMargin by remember { mutableStateOf(s0.keyboard.sideMargin.toString()) }
    var kbHold by remember { mutableStateOf(s0.keyboard.keyHoldMs.toString()) }
    var autoCap by remember { mutableStateOf(s0.keyboard.autoCapitalize) }
    var symRet by remember { mutableStateOf(s0.keyboard.symbolsReturnOnSpace) }
    var trayPan by remember { mutableStateOf(s0.trayPose.pan.toString()) }
    var trayTilt by remember { mutableStateOf(s0.trayPose.tilt.toString()) }
    var userPan by remember { mutableStateOf(s0.userPose.pan.toString()) }
    var userTilt by remember { mutableStateOf(s0.userPose.tilt.toString()) }
    var wake by remember { mutableStateOf(s0.wakeWordEnabled) }
    var tts by remember { mutableStateOf(s0.ttsEnabled) }
    var eyes by remember { mutableStateOf(s0.eyesTrackMotion) }
    var webPort by remember { mutableStateOf(s0.webPort.toString()) }
    var transportMenu by remember { mutableStateOf(false) }
    var profileMenu by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }

    ScreenScaffold("Settings", onBack) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())) {
            Section("Agent (Claude)")
            OutlinedTextField(
                apiKey, { apiKey = it }, label = { Text("Anthropic API key") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(model, { model = it }, label = { Text("Model") }, singleLine = true, modifier = Modifier.fillMaxWidth())

            Section("Body link")
            Box {
                OutlinedButton(onClick = { transportMenu = true }) { Text(transport.label) }
                DropdownMenu(transportMenu, { transportMenu = false }) {
                    TransportChoice.entries.forEach { t ->
                        DropdownMenuItem(text = { Text(t.label) }, onClick = { transport = t; transportMenu = false })
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(simHost, { simHost = it }, label = { Text("Simulator host") }, singleLine = true, modifier = Modifier.weight(2f))
                NumField("Port", simPort, { simPort = it }, Modifier.weight(1f))
            }
            Text("Emulator → host machine is 10.0.2.2. Run `tappet sim` on the PC.", style = MaterialTheme.typography.bodySmall, color = Color.Gray)

            Section("Target device")
            Box {
                OutlinedButton(onClick = { profileMenu = true }) { Text(profile) }
                DropdownMenu(profileMenu, { profileMenu = false }) {
                    c.deviceProfiles.forEach { p ->
                        DropdownMenuItem(text = { Text(p.name) }, onClick = { profile = p.name; profileMenu = false })
                    }
                }
            }

            Section("Keyboard layout (screen-normalized v)")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumField("Top of key rows", kbTop, { kbTop = it }, Modifier.weight(1f))
                NumField("Bottom of key rows", kbBottom, { kbBottom = it }, Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumField("Side margin (u)", kbMargin, { kbMargin = it }, Modifier.weight(1f))
                NumField("Key hold ms", kbHold, { kbHold = it }, Modifier.weight(1f))
            }
            Toggle("Auto-capitalization at start / after sentences", autoCap) { autoCap = it }
            Toggle("123 layer returns to letters after space", symRet) { symRet = it }

            Section("Neck poses (degrees; +pan = robot's left, −tilt = down)")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumField("Tray pan", trayPan, { trayPan = it }, Modifier.weight(1f))
                NumField("Tray tilt", trayTilt, { trayTilt = it }, Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumField("User pan", userPan, { userPan = it }, Modifier.weight(1f))
                NumField("User tilt", userTilt, { userTilt = it }, Modifier.weight(1f))
            }

            Section("Behaviour")
            Toggle("Listen for \"hey tappet\"", wake) { wake = it }
            Toggle("Speak replies", tts) { tts = it }
            Toggle("Eyes follow motion", eyes) { eyes = it }
            NumField("Web server port", webPort, { webPort = it })

            msg?.let { Text(it, color = Accent, modifier = Modifier.padding(top = 8.dp)) }
            Row(Modifier.padding(vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    val before = c.settings.value
                    try {
                        c.settings.update {
                            it.copy(
                                apiKey = apiKey, model = model, transport = transport, simHost = simHost,
                                simPort = simPort.toInt(), deviceProfileName = profile,
                                keyboard = KeyboardParams(kbTop.toDouble(), kbBottom.toDouble(), kbMargin.toDouble(), autoCap, symRet, kbHold.toInt()),
                                trayPose = NeckPose(trayPan.toDouble(), trayTilt.toDouble()),
                                userPose = NeckPose(userPan.toDouble(), userTilt.toDouble()),
                                wakeWordEnabled = wake, ttsEnabled = tts, eyesTrackMotion = eyes, webPort = webPort.toInt(),
                            )
                        }
                        val after = c.settings.value
                        if (after.transport != before.transport || after.simHost != before.simHost || after.simPort != before.simPort) c.client.reconnect()
                        msg = if (after.deviceProfileName != before.deviceProfileName) {
                            c.calibration.resetToDefault(c.currentProfile())
                            "Saved. Screen calibration reset to the ${after.deviceProfileName} default — re-run auto calibration."
                        } else "Saved."
                    } catch (e: NumberFormatException) {
                        msg = "Invalid number: ${e.message}"
                    }
                }) { Text("Save") }
                OutlinedButton(onClick = { c.pairing.revokeAll(); msg = "All paired browsers revoked; new PIN shown." }) { Text("Revoke pairings") }
            }
        }
    }
}
