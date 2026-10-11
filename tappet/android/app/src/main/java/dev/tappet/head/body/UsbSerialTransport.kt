package dev.tappet.head.body

import android.content.Context
import android.hardware.usb.UsbManager
import com.hoho.android.usbserial.driver.CdcAcmSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/**
 * USB-serial transport: the phone is USB host (OTG adapter) and talks to the DevKit's UART bridge
 * (CP210x / CH34x / FTDI) or the ESP32-S3 native USB-CDC port at 115200 8N1.
 */
class UsbSerialTransport(private val context: Context, private val baud: Int) : StreamBodyLink() {
    private val usb = context.getSystemService(Context.USB_SERVICE) as UsbManager
    @Volatile private var port: UsbSerialPort? = null
    override var name = "usb-serial"
        private set

    override suspend fun open() {
        val driver = findDriver(context) ?: throw IOException("no USB serial device attached")
        if (!UsbPermission.request(context, usb, driver.device)) throw IOException("USB device permission denied")
        withContext(Dispatchers.IO) {
            resetBuffers()
            val conn = usb.openDevice(driver.device) ?: throw IOException("openDevice failed")
            val p = driver.ports.first()
            p.open(conn)
            p.setParameters(baud, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
            // DTR/RTS both deasserted keeps the ESP32 auto-reset circuit out of reset/bootloader.
            runCatching { p.setDTR(false); p.setRTS(false) }
            port = p
            name = "usb-serial ${driver.javaClass.simpleName.removeSuffix("SerialDriver")}"
            input = PortInputStream(p)
            output = PortOutputStream(p)
        }
    }

    override fun close() {
        runCatching { port?.close() }
        port = null
        input = null
        output = null
    }

    private class PortInputStream(private val p: UsbSerialPort) : InputStream() {
        private val one = ByteArray(1)
        override fun read(): Int = if (read(one, 0, 1) <= 0) -1 else one[0].toInt() and 0xff
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (!p.isOpen) return -1
            val tmp = if (off == 0 && len >= maxOf(64, p.readEndpoint?.maxPacketSize ?: 64)) b else ByteArray(maxOf(len, 64))
            val n = try { p.read(tmp, 200) } catch (e: IOException) { if (!p.isOpen) return -1 else throw e }
            if (n > 0 && tmp !== b) System.arraycopy(tmp, 0, b, off, minOf(n, len))
            return n.coerceAtLeast(0)
        }
    }

    private class PortOutputStream(private val p: UsbSerialPort) : OutputStream() {
        override fun write(b: Int) = write(byteArrayOf(b.toByte()), 0, 1)
        override fun write(b: ByteArray, off: Int, len: Int) {
            p.write(if (off == 0 && len == b.size) b else b.copyOfRange(off, off + len), 1000)
        }
    }

    companion object {
        private val prober: UsbSerialProber by lazy {
            val table = UsbSerialProber.getDefaultProbeTable()
            // Espressif native USB (ESP32-S3 USB-Serial/JTAG and TinyUSB CDC)
            table.addProduct(0x303A, 0x1001, CdcAcmSerialDriver::class.java)
            table.addProduct(0x303A, 0x4001, CdcAcmSerialDriver::class.java)
            table.addProduct(0x303A, 0x0002, CdcAcmSerialDriver::class.java)
            UsbSerialProber(table)
        }

        fun findDriver(context: Context): UsbSerialDriver? {
            val usb = context.getSystemService(Context.USB_SERVICE) as UsbManager
            return prober.findAllDrivers(usb).firstOrNull()
        }
    }
}
