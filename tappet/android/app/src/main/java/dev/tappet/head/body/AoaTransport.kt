package dev.tappet.head.body

import android.content.Context
import android.hardware.usb.UsbAccessory
import android.hardware.usb.UsbManager
import android.os.ParcelFileDescriptor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException

/**
 * Android Open Accessory transport (SPEC §5): the ESP32-S3 is the USB host and has switched the phone
 * into accessory mode with manufacturer "Tappet" / model "TappetBody". We open the accessory's
 * ParcelFileDescriptor and stream protocol lines over the bulk endpoints.
 */
class AoaTransport(
    private val context: Context,
    private val manufacturer: String,
    private val model: String,
) : StreamBodyLink() {
    private val usb = context.getSystemService(Context.USB_SERVICE) as UsbManager
    @Volatile private var pfd: ParcelFileDescriptor? = null
    override val name = "aoa"

    fun findAccessory(): UsbAccessory? =
        usb.accessoryList?.firstOrNull { it.manufacturer == manufacturer && it.model == model }

    override suspend fun open() {
        val acc = findAccessory() ?: throw IOException("no $manufacturer/$model accessory attached")
        if (!UsbPermission.request(context, usb, acc)) throw IOException("USB accessory permission denied")
        withContext(Dispatchers.IO) {
            resetBuffers()
            val fd = usb.openAccessory(acc) ?: throw IOException("openAccessory failed (already open by another app?)")
            pfd = fd
            input = FileInputStream(fd.fileDescriptor)
            output = FileOutputStream(fd.fileDescriptor)
        }
    }

    override fun close() {
        runCatching { input?.close() }
        runCatching { output?.close() }
        runCatching { pfd?.close() }
        pfd = null
        input = null
        output = null
    }

    companion object {
        fun isAttached(context: Context, manufacturer: String, model: String): Boolean {
            val usb = context.getSystemService(Context.USB_SERVICE) as UsbManager
            return usb.accessoryList?.any { it.manufacturer == manufacturer && it.model == model } == true
        }
    }
}
