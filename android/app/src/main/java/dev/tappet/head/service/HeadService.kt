package dev.tappet.head.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.usb.UsbManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import dev.tappet.head.R
import dev.tappet.head.ui.MainActivity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/**
 * Foreground service that owns the [HeadController] (body link, web server, agent, voice, camera),
 * so everything keeps running across activity recreation and while the screen is covered.
 */
class HeadService : LifecycleService() {
    private var ctrl: HeadController? = null

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            ctrl?.client?.reconnect()
        }
    }

    override fun onCreate() {
        super.onCreate()
        goForeground("Starting…")
        val c = HeadController(applicationContext)
        ctrl = c
        c.start()
        c.startSensors(this)
        _controller.value = c
        ContextCompat.registerReceiver(
            this, usbReceiver,
            IntentFilter().apply {
                addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
                addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
                addAction(UsbManager.ACTION_USB_ACCESSORY_DETACHED)
            },
            ContextCompat.RECEIVER_EXPORTED,
        )
        lifecycleScope.launch {
            combine(c.client.status, c.pairing.pin) { st, pin ->
                val link = if (st.connected) "Body: ${st.transport}" else "Body: ${st.state.name.lowercase()}"
                "$link · ${c.webUrl()} · PIN $pin"
            }.distinctUntilChanged().collect { goForeground(it) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_PERMISSIONS_CHANGED -> {
                goForeground(null)
                ctrl?.startSensors(this)
            }
            ACTION_USB_ATTACHED -> ctrl?.client?.reconnect()
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(usbReceiver) }
        _controller.value = null
        ctrl?.stop()
        ctrl = null
        super.onDestroy()
    }

    private var lastText = "Starting…"

    private fun granted(p: String) = ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    private fun goForeground(text: String?) {
        if (text != null) lastText = text
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Tappet head", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_tappet)
            .setContentTitle("Tappet is running")
            .setContentText(lastText)
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        var types = 0
        if (Build.VERSION.SDK_INT >= 29) {
            types = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            if (Build.VERSION.SDK_INT >= 30) {
                if (granted(Manifest.permission.CAMERA)) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
                if (granted(Manifest.permission.RECORD_AUDIO)) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            }
        }
        ServiceCompat.startForeground(this, NOTIFICATION_ID, n, types)
    }

    companion object {
        const val CHANNEL = "tappet_head"
        const val NOTIFICATION_ID = 1
        const val ACTION_PERMISSIONS_CHANGED = "dev.tappet.head.PERMISSIONS_CHANGED"
        const val ACTION_USB_ATTACHED = "dev.tappet.head.USB_ATTACHED"
        const val ACTION_STOP = "dev.tappet.head.STOP"

        private val _controller = MutableStateFlow<HeadController?>(null)
        val controller: StateFlow<HeadController?> = _controller.asStateFlow()

        fun start(context: Context, action: String? = null) {
            val i = Intent(context, HeadService::class.java).setAction(action)
            ContextCompat.startForegroundService(context, i)
        }
    }
}
