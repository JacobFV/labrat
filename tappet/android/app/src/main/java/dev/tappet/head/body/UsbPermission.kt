package dev.tappet.head.body

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbAccessory
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/** Suspending USB permission requests for accessories and devices. */
object UsbPermission {
    const val ACTION = "dev.tappet.head.USB_PERMISSION"

    private fun pendingIntent(context: Context): PendingIntent {
        val intent = Intent(ACTION).setPackage(context.packageName)
        val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        return PendingIntent.getBroadcast(context, 0, intent, flags or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    private suspend fun await(context: Context, request: (PendingIntent) -> Unit): Boolean =
        withTimeoutOrNull(60_000) {
            suspendCancellableCoroutine { cont ->
                val receiver = object : BroadcastReceiver() {
                    override fun onReceive(c: Context, intent: Intent) {
                        runCatching { context.unregisterReceiver(this) }
                        if (cont.isActive) cont.resume(intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false))
                    }
                }
                ContextCompat.registerReceiver(context, receiver, IntentFilter(ACTION), ContextCompat.RECEIVER_NOT_EXPORTED)
                cont.invokeOnCancellation { runCatching { context.unregisterReceiver(receiver) } }
                request(pendingIntent(context))
            }
        } ?: false

    suspend fun request(context: Context, manager: UsbManager, accessory: UsbAccessory): Boolean {
        if (manager.hasPermission(accessory)) return true
        return await(context) { manager.requestPermission(accessory, it) }
    }

    suspend fun request(context: Context, manager: UsbManager, device: UsbDevice): Boolean {
        if (manager.hasPermission(device)) return true
        return await(context) { manager.requestPermission(device, it) }
    }
}
