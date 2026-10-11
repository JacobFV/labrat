package dev.tappet.head.agent

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import dev.tappet.head.R
import dev.tappet.head.ui.MainActivity
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/** Timers and reminders via AlarmManager; fired alarms post a notification and are spoken by the head. */
object Reminders {
    const val CHANNEL = "tappet_reminders"
    const val EXTRA_TEXT = "text"
    const val EXTRA_KIND = "kind"

    /** Hook set by HeadService so fired alarms can be spoken / shown on the face. */
    @Volatile var onFired: ((kind: String, text: String) -> Unit)? = null

    fun ensureChannel(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Timers & reminders", NotificationManager.IMPORTANCE_HIGH))
        }
    }

    /** Parses ISO-8601 with or without offset (local time zone assumed when absent). */
    fun parseWhen(iso: String): ZonedDateTime {
        val s = iso.trim()
        return runCatching { OffsetDateTime.parse(s).atZoneSameInstant(ZoneId.systemDefault()) }
            .recoverCatching { ZonedDateTime.parse(s) }
            .recoverCatching { LocalDateTime.parse(s).atZone(ZoneId.systemDefault()) }
            .recoverCatching { LocalDateTime.parse(s, DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")).atZone(ZoneId.systemDefault()) }
            .getOrElse { throw IllegalArgumentException("cannot parse time '$iso' (use ISO-8601, e.g. 2026-10-11T15:30:00)") }
    }

    fun schedule(context: Context, atMillis: Long, kind: String, text: String): Int {
        val am = context.getSystemService(AlarmManager::class.java)
        val requestCode = ((atMillis / 1000) xor text.hashCode().toLong()).toInt()
        val intent = Intent(context, ReminderReceiver::class.java)
            .putExtra(EXTRA_TEXT, text).putExtra(EXTRA_KIND, kind)
        val pi = PendingIntent.getBroadcast(context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val exactOk = Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()
        if (exactOk) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pi)
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pi)
        return requestCode
    }

    fun notify(context: Context, kind: String, text: String) {
        ensureChannel(context)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_tappet)
            .setContentTitle(if (kind == "timer") "Timer done" else "Reminder")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(if (kind == "timer") NotificationCompat.CATEGORY_ALARM else NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify((System.currentTimeMillis() % Int.MAX_VALUE).toInt(), n)
    }
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val text = intent.getStringExtra(Reminders.EXTRA_TEXT) ?: "Reminder"
        val kind = intent.getStringExtra(Reminders.EXTRA_KIND) ?: "reminder"
        Reminders.notify(context, kind, text)
        Reminders.onFired?.invoke(kind, text)
    }
}
