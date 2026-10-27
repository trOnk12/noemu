package dev.noemu

import android.os.SystemClock
import android.text.format.DateUtils
import java.net.HttpURLConnection
import java.net.URL
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.annotation.LooperMode

/**
 * Checks of the driver's clock (RealClock) and network switch (Network) inside a real Robolectric run, with the driver
 * as noemu adds it. Run by probe.sh; prints PROBE lines. Expects the repository's relay on 127.0.0.1:18080.
 */
@RunWith(androidx.test.ext.junit.runners.AndroidJUnit4::class)
@LooperMode(LooperMode.Mode.PAUSED)
@Config(sdk = [36])
class Probe {
    private fun get(url: String) = runCatching {
        (URL(url).openConnection() as HttpURLConnection).run { connectTimeout = 15000; readTimeout = 25000; "HTTP $responseCode" }
    }.getOrElse { "${it.javaClass.simpleName}: ${it.message}" }

    @Test fun clock() {
        val now = System.currentTimeMillis()  // this class isn't instrumented: the real time
        println("PROBE real now $now, framework now ${android.app.Notification().`when`}, uptime ${SystemClock.uptimeMillis()}")
        println("PROBE a minute ago: " + DateUtils.getRelativeTimeSpanString(now - 60_000))
    }

    @Test fun network() {
        for (u in listOf("https://pypi.org/simple/", "https://repo1.maven.org/maven2/", "http://127.0.0.1:18080/health"))
            println("PROBE on: $u -> ${get(u)}")
        val t0 = System.currentTimeMillis()
        val poll = kotlin.concurrent.thread {
            val r = get("http://127.0.0.1:18080/v1/sessions/probe/observations?after=0&limit=1&waitMs=20000")
            println("PROBE a 20 s long poll, network off after 1 s -> $r after ${System.currentTimeMillis() - t0} ms")
        }
        Thread.sleep(1000)
        Network.set(false)
        poll.join()
        println("PROBE off: https://repo1.maven.org/maven2/ -> ${get("https://repo1.maven.org/maven2/")}")
        Network.set(true)
        println("PROBE on again: https://repo1.maven.org/maven2/ -> ${get("https://repo1.maven.org/maven2/")}")
    }
}
