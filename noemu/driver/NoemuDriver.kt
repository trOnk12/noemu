package dev.noemu

import android.app.Activity
import android.content.ContentProvider
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Rect
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.TextView
import org.robolectric.android.controller.ActivityController
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Implements
import org.robolectric.annotation.LooperMode
import org.robolectric.annotation.RealObject
import org.robolectric.shadows.ShadowApplication
import org.robolectric.shadows.ShadowLog
import org.robolectric.shadows.ShadowLooper
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.PrintWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.time.Duration

/**
 * Runs the app on the JVM (Robolectric) and serves one command per line on localhost:
 *   screen | tap N|TEXT | type N|TEXT <text> | scroll up|down [N] | back | wait MS | shot FILE | logs | quit
 * Each reply is text, ending with a line "." .
 */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@LooperMode(LooperMode.Mode.PAUSED)
@Config(sdk = [36], qualifiers = "w411dp-h891dp-xxhdpi", shadows = [ProvidersFirst::class, BindsOwnServices::class, AllFilesAccess::class])
class NoemuDriver {
    /** The back stack, as the system would keep it: Robolectric records startActivity() but doesn't launch. */
    private val stack = mutableListOf<ActivityController<out Activity>>()
    private lateinit var app: android.app.Application
    private val backgroundCrashes = mutableListOf<Throwable>()  // on a device, these kill the app too
    /** Package prefix of the app's own code (the application id can carry a suffix like ".debug"). */
    private var code = ""
    private lateinit var launch: Intent
    private var elements: List<Element> = emptyList()
    private val out = File(System.getProperty("noemu.out") ?: ".gradle/noemu").apply { mkdirs() }

    @Test fun serve() {
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            System.err.println("noemu: thread ${t.name} crashed: $e")
            synchronized(backgroundCrashes) { backgroundCrashes += e }
        }
        ShadowLog.stream = null
        // Frames as messages every 16 ms of virtual time, as on a device; otherwise an endless animation turns
        // vsync into an endless synchronous loop.
        org.robolectric.shadows.ShadowChoreographer.setPaused(true)
        org.robolectric.shadows.ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        app = ApplicationProvider.getApplicationContext()
        launch = System.getProperty("noemu.activity")?.let { Intent().setClassName(app, it) }
            ?: app.packageManager.getLaunchIntentForPackage(app.packageName)
            ?: error("no launcher activity; pass --activity")
        System.getProperty("noemu.data")?.let { launch.setAction(Intent.ACTION_VIEW).setData(android.net.Uri.parse(it)) }
        // A crash at launch is the app's answer to `start`: keep its whole trace where noemu.py prints it.
        runCatching { open(Intent(launch)) }.onFailure { File(out, "crash.txt").writeText(it.stackTraceToString()); throw it }
        code = listOfNotNull(stack.lastOrNull()?.get()?.javaClass?.name, app.javaClass.name.takeUnless { it.startsWith("android.") })
            .map { it.split(".").dropLast(1) }
            .reduce { a, b -> a.zip(b).takeWhile { (x, y) -> x == y }.map { it.first } }
            .take(3).joinToString(".").ifBlank { app.packageName }
        stable(15000)
        val server = ServerSocket(System.getProperty("noemu.port", "0").toInt(), 1, InetAddress.getLoopbackAddress())
        File(out, "port").writeText(server.localPort.toString())
        println("noemu: listening on ${server.localPort}")
        while (true) {
            val socket = server.accept()
            val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
            val writer = PrintWriter(socket.getOutputStream(), true)
            val line = reader.readLine() ?: continue
            if (line.trim() == "quit") {
                writer.println("bye\n."); socket.close(); break
            }
            var reply = runCatching { handle(line.trim()) }.getOrElse { System.err.println(it.stackTraceToString()); "error: $it" }
            // A crash on another thread or in a posted task, which the command itself didn't see.
            synchronized(backgroundCrashes) { backgroundCrashes.toList().also { backgroundCrashes.clear() } }
                .forEach { e -> reply = "the app crashed: ${generateSequence(e) { it.cause }.last()}\n" + reply }
            writer.println(reply); writer.println("."); socket.close()
        }
        server.close()
    }

    private fun handle(line: String): String {
        val (cmd, rest) = line.split(" ", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        return when (cmd) {
            "screen" -> screen()
            "tap" -> act(rest) { tap(it) }
            "type" -> {
                // noemu.py separates the target from the text with a tab, so either may contain spaces.
                val (target, text) = rest.split(if ('\t' in rest) "\t" else " ", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
                act(target) { type(it, text) }
            }
            "scroll" -> scroll(rest)
            "back" -> { onActivity<Unit> { back(it) }; stable(); screen() }
            "leaks" -> {
                elements = emptyList()
                leaked().joinToString("\n\n") { (_, text, app) -> if (app) text else "$text (not the app's)" }
                    .ifEmpty { "no destroyed activity is still in memory" }
            }
            "kill" -> { killAndRestore(); stable(); "killed in the background and reopened (saved state ${savedBytes / 1024} KB)\n" + screen() }
            "rotate" -> { repeat(rest.toIntOrNull() ?: 1) { rotate(); stable(2500) }; "now ${if (landscape) "landscape" else "portrait"}\n" + screen() }
            "wait" -> { settle(rest.toLongOrNull() ?: 1000); stable(); screen() }
            "shot" -> shot(File(rest.ifBlank { File(out, "screen.png").path }))
            "crawl" -> rest.split(" ").let { a -> crawl(a[0].toIntOrNull() ?: 200, a.getOrNull(1)?.toLongOrNull() ?: 10) }
            "logs" -> ShadowLog.getLogs().takeLast(60).joinToString("\n") { "${it.type.toLevel()} ${it.tag}: ${it.msg}" + (it.throwable?.let { e -> "\n  $e" } ?: "") }
            else -> "unknown command: $cmd (screen, tap, type, scroll, back, rotate, kill, leaks, wait, shot, logs, crawl, quit)"
        }
    }

    // ---------------------------------------------------------------------------------------------- the screen
    class Element(val label: String, val kind: String, val bounds: Rect, val click: (() -> Unit)?, val setText: ((String) -> Unit)?,
                  val scroll: ((Int) -> Unit)?, val state: String? = null)

    private fun screen(): String {
        elements = collect()
        val name = onActivity { it.javaClass.simpleName }
        val lines = elements.mapIndexed { i, e ->
            val can = listOfNotNull("tap".takeIf { e.click != null }, "type".takeIf { e.setText != null }, "scroll".takeIf { e.scroll != null })
            "[$i] ${e.kind} ${e.label.take(80).ifBlank { "(no label)" }}${e.state?.let { " [$it]" } ?: ""}  @${e.bounds.centerX()},${e.bounds.centerY()}" +
                if (can.isEmpty()) "" else "  (${can.joinToString()})"
        }
        val shot = shot(File(out, "screen.png"))
        return "activity $name\n" + lines.joinToString("\n") + "\n" + shot
    }

    private fun collect(activityOnly: Boolean = false): List<Element> {
        val found = mutableListOf<Element>()
        var screen = Rect()
        onActivity { a ->
            screen = Rect(0, 0, a.window.decorView.width, a.window.decorView.height)
            // A dialog or a menu takes the touches: what lies under it can't be tapped on a device.
            val touchable = windows().lastOrNull { w ->
                (w.layoutParams as? android.view.WindowManager.LayoutParams)?.let { it.flags and android.view.WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE == 0 } ?: true
            }
            (if (activityOnly) listOf(a.window.decorView) else listOfNotNull(touchable ?: a.window.decorView)).forEach { walk(it, found) }
        }
        // Visible ones only: pages of a pager or rows scrolled away are still in the tree.
        return found.filter { it.bounds.width() > 0 && it.bounds.height() > 0 && Rect.intersects(it.bounds, screen) }
    }

    /** Every window on screen, bottom to top: the activity, then dialogs, popups, menus. */
    @Suppress("UNCHECKED_CAST")
    private fun windows(): List<View> = runCatching {
        val global = Class.forName("android.view.WindowManagerGlobal")
        val instance = global.getMethod("getInstance").invoke(null)
        val field = global.getDeclaredField("mViews").apply { isAccessible = true }
        val top = stack.lastOrNull()?.get()
        // On a device, finishing an activity takes its dialogs with it; Robolectric leaves them around. Show the top
        // activity's windows and the app's own (toasts), not those of activities further down or gone.
        (field.get(instance) as List<View>).filter { it.isAttachedToWindow && it.visibility == View.VISIBLE }
            .filter { v -> activityOf(v.context).let { it == null || it === top } }
    }.getOrElse { listOf(onActivity { it.window.decorView }) }

    private fun activityOf(context: android.content.Context?): Activity? {
        var c = context
        while (c is android.content.ContextWrapper) {
            if (c is Activity) return c
            c = c.baseContext
        }
        return null
    }

    private fun walk(view: View, found: MutableList<Element>) {
        if (view.visibility != View.VISIBLE) return
        if (ComposeReader.read(view, found)) return  // a Compose root: its semantics tree
        val r = Rect().also { view.getGlobalVisibleRect(it) }
        val label = when (view) {
            is EditText -> view.text?.toString().orEmpty().ifBlank { view.hint?.toString().orEmpty() }
            is TextView -> view.text?.toString().orEmpty()
            else -> view.contentDescription?.toString().orEmpty()
        }
        val kind = when (view) { is EditText -> "field"; is TextView -> if (view.isClickable) "button" else "text"; else -> if (view.isClickable) "button" else "view" }
        if (label.isNotBlank() || view.isClickable || view is EditText) {
            val state = (view as? android.widget.Checkable)?.let { if (it.isChecked) "on" else "off" }
                ?: if (view.isSelected) "selected" else null
            found += Element(label.ifBlank { view.contentDescription?.toString().orEmpty() }, kind, r, state = state,
                click = if (view.isClickable) ({ view.performClick() }) else null,
                setText = if (view is EditText) ({ t -> view.setText(t) }) else null,
                scroll = if (view.canScrollVertically(1) || view.canScrollVertically(-1)) ({ dy -> view.scrollBy(0, dy) }) else null)
        }
        if (view is ViewGroup) for (i in 0 until view.childCount) walk(view.getChildAt(i), found)
    }

    // -------------------------------------------------------------------------------------------------- actions
    private fun find(target: String): Element? {
        if (elements.isEmpty()) elements = collect()
        target.toIntOrNull()?.let { return elements.getOrNull(it) }
        val all = collect()
        return all.firstOrNull { it.label.equals(target, true) && (it.click != null || it.setText != null) }
            ?: all.firstOrNull { it.label.contains(target, true) && (it.click != null || it.setText != null) }
            ?: all.firstOrNull { it.label.contains(target, true) }
    }

    private fun act(target: String, action: (Element) -> String): String {
        val e = find(target) ?: return "nothing on screen matches \"$target\"\n" + screen()
        val result = action(e)
        stable()
        return result + "\n" + screen()
    }

    private fun tap(e: Element): String {
        val click = e.click ?: return "\"${e.label}\" can't be tapped"
        onActivity { click() }
        return "tapped \"${e.label}\""
    }

    private fun type(e: Element, text: String): String {
        val set = e.setText ?: return "\"${e.label}\" isn't a text field"
        onActivity { set(text) }
        return "typed into \"${e.label}\""
    }

    private fun scroll(arg: String): String {
        val parts = arg.split(" ")
        val dy = (if (parts.getOrNull(0) == "up") -1 else 1) * 1200
        val list = parts.getOrNull(1)?.toIntOrNull()?.let { elements.getOrNull(it) } ?: collect().firstOrNull { it.scroll != null }
            ?: return "nothing scrollable"
        onActivity { list.scroll!!(dy) }
        settle()
        return "scrolled ${parts.getOrNull(0) ?: "down"}\n" + screen()
    }

    // ------------------------------------------------------------------------------------------------- crawling
    /**
     * Explores the app: on each screen, taps every element not tried there yet (typing into fields first), goes
     * back when a screen is done, rotates the device there and back, and records each uncaught exception and each
     * screen that doesn't survive the rotation, with the taps that led to it.
     */
    private fun crawl(steps: Int, minutes: Long = 10): String {
        val tried = mutableMapOf<String, MutableSet<String>>()
        val screens = mutableSetOf<String>()
        val crashes = linkedMapOf<String, String>()  // first app frame -> report
        val path = mutableListOf<String>()
        var actions = 0
        val limits = linkedSetOf<String>()
        val stateLoss = linkedMapOf<String, String>()
        val toCheck = linkedMapOf<String, String>()
        val leaks = linkedMapOf<String, String>()
        val rotatedOn = mutableMapOf<String, Int>()
        // In a fixed but shuffled order: the first elements of a screen are often its way out (menu, settings).
        val random = kotlin.random.Random(42)
        var lastSig = ""
        var lastContent = ""
        var acted = false
        var lastKeys = emptyList<String>()
        val replay = ArrayDeque<String>()
        val replayedOn = mutableSetOf<String>()
        val killedOn = mutableSetOf<String>()
        fun signature(es: List<Element>) = onActivity { it.javaClass.simpleName } + "|" +
            es.filter { it.click != null || it.setText != null }
                // Typing isn't a new screen, and neither is a ticking clock or a video position: fields and digits are
                // left out of what names the screen.
                .joinToString("|") { if (it.setText != null) "field" else it.label.take(40).replace(Regex("\\d+"), "#") }.hashCode()
        fun restart() {
            stack.toList().reversed().forEach { runCatching { it.pause().stop().destroy() } }
            stack.clear()
            path.clear()
            open(Intent(launch))
            stable(8000)
        }
        // A budget in time too: an app that slows down as it runs (views stacking up) still gets its report.
        val deadline = System.nanoTime() + minutes * 60_000_000_000L
        val crawlLog = File(out, "crawl.log").apply { writeText("") }
        // Elements hold views, and views their activity: outside the loop, so the leak check can let go of them.
        var es: List<Element> = emptyList()
        var fields: List<Element> = emptyList()
        var next: Pair<String, Element>? = null
        var chosen: Pair<String, Element>? = null
        for (step in 0 until steps) {
            if (System.nanoTime() > deadline) break
            if (step % 5 == 4) {
                es = emptyList(); fields = emptyList(); next = null; chosen = null; elements = emptyList()
                leaked().forEach { (key, text, app) ->
                    if (app) leaks.getOrPut(key) { "$text\n  after: ${path.takeLast(8).joinToString(" > ")}" } else limits.add(text)
                }
            }
            es = runCatching { collect() }.getOrElse { emptyList() }
            if (stack.isEmpty()) restart()
            val sig = signature(es)
            screens += sig
            val done = tried.getOrPut(sig) { mutableSetOf() }
            // Rotate after an action that changed something without leaving the screen (a digit typed, a box
            // checked): that is the state a rotation must keep.
            val content = es.joinToString("|") { "${it.label}${it.state}" }
            val changedHere = acted && sig == lastSig && content != lastContent
            // Every screen once when first seen, again after an action changed it, and every so often anyway.
            // After an action changed a screen, once per screen: the app killed in the background and reopened.
            // Typing then tapping changed the screen (a message sent, an item added): do it once more, as users
            // do, before a restart check wipes what the first one made. Doing the same thing twice is where duplicate
            // keys and double submits crash.
            if (changedHere && lastKeys.size == 2 && lastKeys[0].startsWith("type:") && lastKeys[1].startsWith("tap:") && replayedOn.add(sig)) {
                replay.addAll(lastKeys)
            }
            // Not between typing and submitting: a restart there would wipe what the user was about to send.
            val killing = stack.isNotEmpty() && replay.isEmpty() && changedHere &&
                lastKeys.lastOrNull()?.startsWith("type:") != true && killedOn.add(sig)
            val rotating = !killing && stack.isNotEmpty() && replay.isEmpty() &&
                (sig !in rotatedOn || (changedHere && rotatedOn.getValue(sig) < 3) || actions % 10 == 9)
            // A field is known by its place on the screen: its label is its text, which typing changes.
            fields = es.filter { it.setText != null }
            next = if (rotating || killing) null else generateSequence { replay.removeFirstOrNull() }.firstNotNullOfOrNull { key ->
                (if (key.startsWith("type:#")) fields.getOrNull(key.removePrefix("type:#").toInt())
                else es.firstOrNull { it.click != null && "tap:${it.label}" == key })?.let { key to it }
            }
                ?: fields.withIndex().firstOrNull { "type:#${it.index}" !in done }?.let { "type:#${it.index}" to it.value }
                // After typing, submit it, as a user would.
                ?: es.takeIf { lastKeys.lastOrNull()?.startsWith("type:") == true }
                    ?.filter { it.click != null && "tap:${it.label}" !in done && submitRank(it.label) >= 0 }
                    ?.minByOrNull { submitRank(it.label) }?.let { "tap:${it.label}" to it }
                ?: es.filter { it.click != null && "tap:${it.label}" !in done }.randomOrNull(random)?.let { "tap:${it.label}" to it }
            lastSig = sig
            lastContent = content
            acted = !rotating && !killing && next != null
            // Every decision, for whoever wants to see how the crawl went: .gradle/noemu/crawl.log.
            crawlLog.appendText("${actions} ${es.firstOrNull()?.let { onActivity { a -> a.javaClass.simpleName } }} " +
                "changed=$changedHere ${if (killing) "kill" else if (rotating) "rotate" else next?.first ?: "back"} replay=${replay.toList()}\n")
            chosen = next
            val outcome = runCatching {
                if (killing) {
                    lastKeys = emptyList()
                    val before = snapshot()
                    path += "kill"; killAndRestore(); stable(2500)
                    val after = snapshot()
                    rotationFinding(before, after, "the app was killed in the background and reopened")?.let { f ->
                        (if (f.sure) stateLoss else toCheck).getOrPut(f.key) { "${f.text}\n  after: ${path.takeLast(8).joinToString(" > ")}" }
                    }
                    // The system carries saved state across processes in one binder transaction (about 1 MB, shared).
                    if (savedBytes > 500 * 1024) stateLoss.getOrPut("${before.activity} saved state") {
                        "${before.activity}: saved state is ${savedBytes / 1024} KB; above roughly 500 KB, saving it can crash " +
                            "with TransactionTooLargeException\n  after: ${path.takeLast(8).joinToString(" > ")}"
                    }
                } else if (rotating) {
                    // A rotation there and back: the activity is rebuilt twice from its saved state (and once with
                    // the other orientation's layouts, where many crashes hide). Back in the first orientation the
                    // screen should be what it was; what changed is state the app forgot to save.
                    rotatedOn[sig] = (rotatedOn[sig] ?: 0) + 1
                    val before = snapshot()
                    path += "rotate"; rotate(); stable(2500)
                    path += "rotate"; rotate(); stable(2500)
                    val after = snapshot()
                    System.err.println("noemu: rotated ${before.activity}: ${before.all.size} -> ${after.all.size} elements, gone ${(before.all.toSet() - after.all.toSet()).take(5)}")
                    rotationFinding(before, after)?.let { f ->
                        (if (f.sure) stateLoss else toCheck).getOrPut(f.key) { "${f.text}\n  after: ${path.takeLast(8).joinToString(" > ")}" }
                    }
                } else if (chosen == null) {
                    lastKeys = emptyList()
                    if (stack.size > 1 || path.isNotEmpty()) { onActivity<Unit> { back(it) }; path += "back" } else restart()
                } else {
                    done += chosen.first
                    lastKeys = (lastKeys + chosen.first).takeLast(2)
                    path += (if (chosen.first.startsWith("type:")) "type:${chosen.second.label.take(30)}" else chosen.first).replace('\n', ' ')
                    if (chosen.first.startsWith("type:")) chosen.second.setText!!(TYPED) else chosen.second.click!!()
                }
                stable(2500)
            }
            actions++
            val background = synchronized(backgroundCrashes) { backgroundCrashes.toList().also { backgroundCrashes.clear() } }
            // Failures inside Robolectric's own stand-ins (a bound service that never connects, ...) are limits of
            // running on the JVM, not app bugs: note them and carry on.
            val (simulated, real) = (listOfNotNull(outcome.exceptionOrNull()) + background).partition { e ->
                // Where it was thrown: the frames above the app's own code. Robolectric's frames further down (it
                // drives the lifecycle) don't matter.
                val root = generateSequence(e) { it.cause }.last()
                root.stackTrace.takeWhile { !it.className.startsWith(code) }
                    .any { it.className.startsWith("org.robolectric.") || it.className.startsWith("dev.noemu.") } ||
                    // GPU features Robolectric's native graphics can't build.
                    root.stackTrace.firstOrNull()?.className in setOf("android.graphics.MeshSpecification", "android.graphics.Mesh", "android.graphics.RuntimeShader")
            }
            simulated.forEach { e -> limits.add("${e.javaClass.simpleName}: ${e.message?.take(120)}") }
            real.firstOrNull()?.let { e ->
                val root = generateSequence(e) { it.cause }.last()
                val frames = root.stackTrace
                val own = frames.firstOrNull { it.className.startsWith(code) }
                val key = "${root.javaClass.name} at ${own ?: frames.firstOrNull()}"
                if (key !in crashes) {
                    crashes[key] = "${root.javaClass.name}: ${root.message?.take(200)}\n" +
                        "  in ${if (own != null) "app code: $own" else "library/framework: ${frames.firstOrNull()}"}\n" +
                        "  after: ${path.takeLast(8).joinToString(" > ")}\n" +
                        frames.take(12).joinToString("\n") { "    at $it" }
                }
                restart()
            }
            if (stack.isEmpty()) restart()
        }
        return "crawled $actions actions over ${screens.size} distinct screens; ${crashes.size} distinct crash(es), " +
            "${stateLoss.size} state problem(s), ${leaks.size} leak(s)\n\n" +
            (crashes.values + stateLoss.values + leaks.values).joinToString("\n\n") +
            (if (toCheck.isEmpty()) "" else "\n\nworth a look (a rotation or restart changed these; a search bar or menu closing may be fine):\n" +
                toCheck.values.joinToString("\n") { "  " + it.replace("\n", "\n  ") }) +
            if (limits.isEmpty()) "" else "\n\nnot app bugs (things the JVM can't do; ignored):\n" + limits.joinToString("\n") { "  $it" }
    }

    /**
     * The activity's own window as the user sees it, steady elements only (looked at twice, so clocks drop out).
     * Dialogs are left out: a plain dialog closes on rotation on a device too.
     */
    class Snapshot(val activity: String, val all: List<String>, val top: Map<String, Int>)

    private fun snapshot(): Snapshot {
        val whole = onActivity { Rect(0, 0, it.window.decorView.width, it.window.decorView.height) }
        // Wholly on screen only: a row cut at the edge comes and goes with a pixel of scroll.
        fun look() = collect(activityOnly = true).filter { whole.contains(it.bounds) }
            .map { "${it.kind} ${it.label}${it.state?.let { s -> " [$s]" } ?: ""}" to it.bounds.top }
        val first = look()
        settle(1100)  // longer than a second: clocks and media positions tick once a second
        val steady = first.map { it.first }.toSet().intersect(look().map { it.first }.toSet())
        val kept = first.filter { it.first in steady }
        return Snapshot(onActivity { it.javaClass.simpleName }, kept.map { it.first }, kept.reversed().toMap())
    }

    /**
     * Rotating there and back should give the same screen. Gone: text, fields, checks and selections the app didn't save.
     * Repeated: views stacked again on each rebuild (fragments added twice), which slows the app down and in the end
     * crashes it.
     */
    /** What a rotation broke. [sure]: the crawl's own typing lost, or views stacked again; else worth a look. */
    class RotationFinding(val key: String, val text: String, val sure: Boolean)

    private fun rotationFinding(before: Snapshot, after: Snapshot, how: String = "rotating there and back"): RotationFinding? {
        if (before.activity != after.activity) return null
        val was = before.all.groupingBy { it }.eachCount()
        val repeated = after.all.groupingBy { it }.eachCount().filter { (k, n) -> n > (was[k] ?: n) }
        if (repeated.size >= 3) return RotationFinding("${before.activity} repeated $how",
            "${before.activity}: ${before.all.size} elements before $how, ${after.all.size} after; " +
                "shown more than once now: ${repeated.keys.take(4).joinToString(", ") { "\"${it.take(40)}\"" }}", true)
        // If what's on screen both times moved, the screen scrolled on the way (lists keep their item, not their
        // pixel): rows coming and going are scrolling, so only fields and toggles count as lost.
        val scrolled = before.top.any { (k, y) -> after.top[k]?.let { it != y } == true }
        val lost = before.all.filter { it.substringAfter(' ').isNotBlank() }
            .filter { !scrolled || it.startsWith("field ") || it.endsWith("]") }.toSet() - after.all.toSet()
        if (lost.isEmpty()) return null
        // Most of it gone is another screen (single-activity apps navigate inside one activity): worth a look only.
        if (lost.size * 2 > before.all.size) return RotationFinding("${before.activity} changed $how",
            "${before.activity}: a different screen after $how; gone: ${lost.take(4).joinToString(", ") { "\"${it.take(50)}\"" }}", false)
        // What the crawl typed itself is the user's own work, and certainly the app's to keep.
        val typed = lost.filter { it.startsWith("field ") && TYPED in it }
        return RotationFinding("${before.activity} lost $how",
            "${before.activity}: " +
                (if (typed.isEmpty()) "" else "typed text lost: ${typed.joinToString(", ") { "\"${it.removePrefix("field ").take(60)}\"" }}; ") +
                "gone after $how: ${(typed + (lost - typed.toSet())).take(6).joinToString(", ") { "\"${it.take(60)}\"" }}",
            typed.isNotEmpty())
    }

    private val TYPED = "noemu"
    /** How surely a button submits what was typed: lower is surer, -1 not at all. */
    private fun submitRank(label: String): Int {
        val word = label.trim().substringBefore(' ').lowercase()
        return listOf("send", "save", "submit", "post", "done", "ok", "add", "create", "confirm", "apply", "next", "go", "search")
            .indexOf(word)
    }

    private var landscape = false

    /** Turns the device: the configuration changes and the top activity is rebuilt, as on a device. */
    private fun rotate() {
        landscape = !landscape
        org.robolectric.RuntimeEnvironment.setQualifiers(if (landscape) "+land" else "+port")
        val controller = stack.last()
        // An activity that declares it handles rotation (configChanges="orientation|screenSize") isn't rebuilt.
        val rotation = android.content.pm.ActivityInfo.CONFIG_ORIENTATION or android.content.pm.ActivityInfo.CONFIG_SCREEN_SIZE
        val handles = runCatching { app.packageManager.getActivityInfo(controller.get().componentName, 0).configChanges }.getOrDefault(0)
        if (handles and rotation == rotation) {
            controller.configurationChange(app.resources.configuration)
            return
        }
        // Robolectric's recreate() can fail after the new activity is up, when it reports window focus; anything
        // else is the app's.
        val old = controller.get()
        runCatching { controller.recreate() }.onFailure { e ->
            if (e !is NullPointerException || e.stackTrace.none { it.methodName == "windowFocusChanged" }) throw e
        }
        val a = controller.get()
        if (!a.window.decorView.isAttachedToWindow) {
            Activity::class.java.getDeclaredMethod("makeVisible").apply { isAccessible = true }.invoke(a)
        }
        if (old !== a) released(old, "rotating")
    }

    /** Size in bytes of the last saved state handed to the system, as a parcel. */
    private var savedBytes = 0

    /**
     * The app killed in the background and the user coming back: the top activity is rebuilt from its saved state
     * alone, parcelled and unparcelled as the system does. Its ViewModels are gone, as after a real process death;
     * singletons survive here (one JVM), so this finds a subset of what a device would.
     */
    private fun killAndRestore() {
        val old = stack.last()
        val a = old.get()
        val saved = android.os.Bundle()
        old.pause().saveInstanceState(saved).stop()
        val parcel = android.os.Parcel.obtain()
        val restored = try {
            saved.writeToParcel(parcel, 0)
            savedBytes = parcel.dataSize()
            parcel.setDataPosition(0)
            android.os.Bundle.CREATOR.createFromParcel(parcel).apply { classLoader = app.classLoader }
        } finally {
            parcel.recycle()
        }
        old.destroy()
        released(a, "the app was killed in the background")
        @Suppress("UNCHECKED_CAST")
        stack[stack.lastIndex] = Robolectric.buildActivity(a.javaClass as Class<Activity>, a.intent).setup(restored)
    }

    // -------------------------------------------------------------------------------------------------- leaks
    /** Activities the app is done with (rotated away, killed, closed): each should be garbage once the app lets go. */
    private val gone = mutableListOf<Pair<java.lang.ref.WeakReference<Activity>, String>>()
    private val analysed = mutableSetOf<String>()

    private fun released(a: Activity, how: String) {
        // On a device the system removes the windows a destroyed activity left open ("has leaked window"), and they
        // stop holding it; Robolectric keeps them.
        runCatching {
            val global = Class.forName("android.view.WindowManagerGlobal")
            val instance = global.getMethod("getInstance").invoke(null)
            @Suppress("UNCHECKED_CAST")
            val views = (global.getDeclaredField("mViews").apply { isAccessible = true }.get(instance) as List<View>).toList()
            views.filter { activityOf(it.context) === a }.forEach { v ->
                global.getMethod("removeView", View::class.java, Boolean::class.javaPrimitiveType).invoke(instance, v, true)
            }
        }
        gone += java.lang.ref.WeakReference(a) to "${a.javaClass.simpleName} still in memory after $how"
    }

    /**
     * Destroyed activities still reachable once the main thread is idle and the heap collected: something holds them,
     * with their whole screen, and each rotation leaks one more. When there are any, the heap is dumped and LeakCanary's
     * analyzer (Shark) finds what holds each: a chain through the app's own code is an app leak; one through Robolectric
     * and the framework alone is the JVM stand-in, not the app.
     */
    private fun leaked(): List<Triple<String, String, Boolean>> {
        forgetTestBookkeeping()
        ShadowLooper.idleMainLooper()
        repeat(3) { System.gc(); System.runFinalization(); Thread.sleep(30) }
        val alive = gone.filter { it.first.get() != null }.map { it.second }.distinct().filter { analysed.add(it) }
        gone.clear()  // each kind reported once: a heap dump and its analysis take a minute or two
        if (alive.isEmpty()) return emptyList()
        val traces = runCatching { leakTraces() }.getOrElse { e ->
            System.err.println("noemu: leak analysis failed: $e")
            return alive.map { Triple(it, it, false) }
        }
        return alive.map { what ->
            val name = what.substringBefore(" ")
            val trace = traces.firstOrNull { it.second.endsWith(".$name") || it.second == name }
            when {
                trace == null -> Triple(what, "$what (no path from a GC root: likely gone at the next collection)", false)
                trace.third -> Triple(trace.first, "$what, held by:\n${trace.first.prependIndent("    ")}", true)  // one report per chain
                else -> Triple(what, "$what, held by Robolectric or the framework only", false)
            }
        }
    }

    /** Robolectric remembers the latest dialog, popup and toast for tests to inspect; a device keeps no such list. */
    private fun forgetTestBookkeeping() {
        fun clear(owner: Any?, cls: Class<*>, vararg names: String) = names.forEach { name ->
            runCatching {
                val f = cls.getDeclaredField(name).apply { isAccessible = true }
                val value = f.get(owner)
                if (value is MutableCollection<*>) value.clear() else f.set(owner, null)
            }
        }
        clear(org.robolectric.Shadows.shadowOf(app), ShadowApplication::class.java,
            "latestPopupMenu", "latestPopupWindow", "latestListPopupWindow", "shownToasts")
        clear(null, org.robolectric.shadows.ShadowDialog::class.java, "shownDialogs", "latestDialog")
        clear(null, org.robolectric.shadows.ShadowAlertDialog::class.java, "latestAlertDialog")
    }

    /** Leak paths of destroyed activities in a heap dump: (path, activity class, through the app's own code). */
    private fun leakTraces(): List<Triple<String, String, Boolean>> {
        val file = File(out, "heap.hprof").apply { delete() }
        // By reflection: projects that compile tests against android.jar alone don't see the JDK's management API.
        val bean = Class.forName("com.sun.management.HotSpotDiagnosticMXBean")
        val mx = Class.forName("java.lang.management.ManagementFactory")
            .getMethod("getPlatformMXBean", Class::class.java).invoke(null, bean)
        bean.getMethod("dumpHeap", String::class.java, Boolean::class.javaPrimitiveType).invoke(mx, file.path, true)
        try {
            val destroyed = object : shark.FilteringLeakingObjectFinder.LeakingObjectFilter {
                override fun isLeakingObject(heapObject: shark.HeapObject) = heapObject is shark.HeapObject.HeapInstance &&
                    heapObject.instanceOf("android.app.Activity") &&
                    heapObject["android.app.Activity", "mDestroyed"]?.value?.asBoolean == true
            }
            val analysis = shark.HeapAnalyzer(shark.OnAnalysisProgressListener.NO_OP).analyze(
                heapDumpFile = file,
                leakingObjectFinder = shark.FilteringLeakingObjectFinder(listOf(destroyed)),
                referenceMatchers = shark.AndroidReferenceMatchers.appDefaults,
                computeRetainedHeapSize = false,
                objectInspectors = shark.AndroidObjectInspectors.appDefaults,
            )
            if (analysis !is shark.HeapAnalysisSuccess) error("$analysis")
            return analysis.allLeaks.toList().flatMap { it.leakTraces }.map { t ->
                // The part of the chain after the last link through noemu or Robolectric (a shadow, "latestPopupWindow"
                // and the like) is what would hold the activity on a device. It counts when there is no such link,
                // when it starts at the app's Application (alive as long as the app), or at a static field: statics
                // hang off Robolectric's class loader here, off the app's on a device.
                val refs = t.referencePath
                val ours = refs.indexOfLast { r ->
                    r.originObject.className.let { it.startsWith("org.robolectric.") || it.startsWith("dev.noemu.") } ||
                        r.referenceName == "__robo_data__"
                }
                val viaStatics = ours >= 0 && refs[ours].referenceName == "classes" && refs[ours].originObject.className.endsWith("ClassLoader")
                val tail = refs.drop(ours + if (viaStatics) 2 else 1)
                val app = tail.isNotEmpty() && tail.any { it.originObject.className.startsWith(code) } &&
                    (ours < 0 || viaStatics || tail.first().originObject.className == this.app.javaClass.name)
                val chain = (if (ours < 0) refs else tail).joinToString("\n") { "${it.originObject.className}.${it.referenceDisplayName}" } +
                    "\n" + t.leakingObject.className
                Triple(chain, t.leakingObject.className, app)
            }
        } finally {
            file.delete()
        }
    }

    // ---------------------------------------------------------------------------------------------- machinery
    private fun <T> onActivity(block: (Activity) -> T): T {
        val top = stack.lastOrNull()?.get() ?: error("no activity on screen")
        return block(top)
    }

    private fun open(intent: Intent) {
        val component = intent.component ?: intent.resolveActivity(app.packageManager)
            ?: run { System.err.println("noemu: nothing in this app handles $intent"); return }
        // A launcher entry is often an <activity-alias>: start the activity it points at.
        val target = runCatching { app.packageManager.getActivityInfo(component, 0).targetActivity }.getOrNull() ?: component.className
        @Suppress("UNCHECKED_CAST")
        val cls = Class.forName(target, true, app.classLoader) as Class<Activity>
        stack.lastOrNull()?.let { runCatching { it.pause().stop() } }
        stack += Robolectric.buildActivity(cls, intent.setComponent(component)).setup()
    }

    /** Launches activities the app started and drops the ones it finished, like the activity manager would. */
    private fun manage() {
        repeat(10) {
            var changed = false
            while (true) {
                val next = org.robolectric.Shadows.shadowOf(app).nextStartedActivity ?: break
                if (next.component?.packageName != null && next.component!!.packageName != app.packageName) continue
                open(next); changed = true
            }
            val finished = stack.filter { it.get().isFinishing || it.get().isDestroyed }
            for (c in finished) {
                runCatching { c.pause().stop().destroy() }
                stack.remove(c)
                released(c.get(), "it was closed")
                changed = true
            }
            if (finished.isNotEmpty()) stack.lastOrNull()?.let { runCatching { it.restart().resume() } }
            if (!changed) return
            ShadowLooper.idleMainLooper()
        }
    }

    /** Lets the app run until the screen stops changing (same elements twice in a row), at most [maxMs]. */
    private fun stable(maxMs: Long = 4000) {
        val start = System.currentTimeMillis()
        var last = ""
        var same = 0
        while (System.currentTimeMillis() - start < maxMs) {
            settle(300)
            val now = runCatching { collect().joinToString("|") { "${it.kind}:${it.label}:${it.bounds}" } }.getOrDefault("")
            same = if (now == last && now.isNotEmpty()) same + 1 else 0
            if (same >= 2) return
            last = now
        }
    }

    /** Lets the app run: main looper tasks, frames and animations for [ms] of virtual time. */
    private fun settle(ms: Long = 600) {
        // Virtual time for the main thread, real time for background threads (database, network, coroutines on IO),
        // whose results are posted back to the main thread.
        val until = System.currentTimeMillis() + ms / 2
        var left = ms
        while (left > 0 || System.currentTimeMillis() < until) {
            ShadowLooper.idleMainLooper(50, java.util.concurrent.TimeUnit.MILLISECONDS)
            Thread.sleep(25)
            left -= 50
        }
        ShadowLooper.idleMainLooper()
        manage()
    }

    /** The window as drawn now; apps that always animate never go idle, so don't wait for it. */
    private fun shot(file: File): String = onActivity { activity ->
        val view = activity.window.decorView
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        view.draw(canvas)
        for (window in windows().filter { it !== view }) {  // dialogs and popups, dimmed behind like on a device
            val at = IntArray(2).also { window.getLocationOnScreen(it) }
            canvas.drawColor(0x66000000)
            canvas.save(); canvas.translate(at[0].toFloat(), at[1].toFloat()); window.draw(canvas); canvas.restore()
        }
        file.parentFile?.mkdirs()
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        "screenshot ${file.absolutePath}"
    }

    /** ComponentActivity routes back through its dispatcher (Compose BackHandler, navigation); others the old way. */
    private fun back(activity: Activity) {
        val dispatcher = runCatching { activity.javaClass.getMethod("getOnBackPressedDispatcher").invoke(activity) }.getOrNull()
        if (dispatcher != null) dispatcher.javaClass.getMethod("onBackPressed").invoke(dispatcher)
        else @Suppress("DEPRECATION") activity.onBackPressed()
    }

    private fun Int.toLevel() = when (this) { 2 -> "V"; 3 -> "D"; 4 -> "I"; 5 -> "W"; 6 -> "E"; else -> "?" }
}

/**
 * Reads Compose screens through their semantics tree, by reflection: the driver then compiles against any app, and
 * the property names it reads ("Text", "OnClick", ...) are stable across Compose versions.
 */
object ComposeReader {
    private fun call(target: Any?, name: String): Any? =
        target?.javaClass?.methods?.firstOrNull { it.name == name && it.parameterCount == 0 }?.invoke(target)

    private fun isComposeRoot(view: View): Boolean {
        var c: Class<*>? = view.javaClass
        while (c != null) {
            if (c.interfaces.any { it.name == "androidx.compose.ui.platform.ViewRootForTest" }) return true
            c = c.superclass
        }
        return false
    }

    fun read(view: View, found: MutableList<NoemuDriver.Element>): Boolean {
        if (!isComposeRoot(view)) return false
        val root = call(call(view, "getSemanticsOwner"), "getRootSemanticsNode") ?: return false
        node(root, found)
        return true
    }

    private fun props(node: Any): Map<String, Any?> {
        val config = call(node, "getConfig") as? Iterable<*> ?: return emptyMap()
        return config.mapNotNull { e -> (e as? Map.Entry<*, *>)?.let { (call(it.key, "getName") as? String ?: return@let null) to it.value } }.toMap()
    }

    private fun text(v: Any?): String? = when (v) {
        null -> null
        is List<*> -> v.joinToString(" ") { it.toString() }.ifBlank { null }
        else -> v.toString().ifBlank { null }
    }

    private fun action(v: Any?): Any? = call(v, "getAction")

    private fun invoke(fn: Any?, vararg args: Any?): Any? =
        fn?.javaClass?.methods?.firstOrNull { it.name == "invoke" && it.parameterCount == args.size }?.apply { isAccessible = true }?.invoke(fn, *args)

    private fun annotated(s: String): Any? {
        val cls = runCatching { Class.forName("androidx.compose.ui.text.AnnotatedString") }.getOrNull() ?: return s
        cls.constructors.firstOrNull { it.parameterCount == 1 && it.parameterTypes[0] == String::class.java }?.let { return it.newInstance(s) }
        cls.constructors.firstOrNull { c -> c.parameterTypes.firstOrNull() == String::class.java && c.parameterTypes.drop(1).all { it == List::class.java } }
            ?.let { c -> return c.newInstance(s, *Array<Any?>(c.parameterCount - 1) { emptyList<Any>() }) }
        return s
    }

    private fun node(node: Any, found: MutableList<NoemuDriver.Element>) {
        val p = props(node)
        val label = listOfNotNull(text(p["ContentDescription"]), text(p["Text"]), text(p["EditableText"]))
            .map { it.trim() }.filter { it.isNotEmpty() }.distinctBy { it.lowercase() }.joinToString(" ")
        val click = action(p["OnClick"])
        val setText = action(p["SetText"])
        val scrollBy = action(p["ScrollBy"])
        val role = p["Role"]?.toString()?.lowercase()
        val kind = when {
            setText != null -> "field"
            role != null -> role
            click != null -> "button"
            scrollBy != null -> "list"
            else -> "text"
        }
        if (label.isNotBlank() || click != null || setText != null || scrollBy != null) {
            val b = call(node, "getBoundsInWindow")
            fun edge(n: String) = (call(b, n) as? Float ?: 0f).toInt()
            val state = p["ToggleableState"]?.toString()?.lowercase()?.let { if (it == "indeterminate") "partly" else it }
                ?: (p["Selected"] as? Boolean)?.let { if (it) "selected" else null }
            found += NoemuDriver.Element(label, kind, Rect(edge("getLeft"), edge("getTop"), edge("getRight"), edge("getBottom")), state = state,
                click = click?.let { f -> { invoke(f); Unit } },
                setText = setText?.let { f -> { t: String -> invoke(f, annotated(t)); Unit } },
                scroll = scrollBy?.let { f -> { dy: Int -> invoke(f, 0f, dy.toFloat()); Unit } })
        }
        // Children of a clickable node (a button's label) belong to it.
        if (click == null) (call(node, "getChildren") as? List<*>)?.filterNotNull()?.forEach { node(it, found) }
    }
}

/**
 * Android creates an app's ContentProviders (App Startup, WorkManager, Firebase, ...) before Application.onCreate;
 * Robolectric doesn't. Apps call super.onCreate() first, so create them there.
 */
@Implements(android.app.Application::class)
class ProvidersFirst : ShadowApplication() {
    @RealObject lateinit var app: android.app.Application

    @Implementation
    protected fun onCreate() {
        online(app)
        // As a user would at the first prompt: no permission dialog stands between the crawl and the app.
        app.packageManager.getPackageInfo(app.packageName, PackageManager.GET_PERMISSIONS).requestedPermissions
            ?.let { grantPermissions(*it) }
        val info = app.packageManager.getPackageInfo(app.packageName, PackageManager.GET_PROVIDERS)
        for (provider in info.providers.orEmpty()) {
            runCatching {
                @Suppress("UNCHECKED_CAST")
                val cls = Class.forName(provider.name, true, app.classLoader) as Class<ContentProvider>
                Robolectric.buildContentProvider(cls).create(provider)
            }.onFailure { System.err.println("noemu: provider ${provider.name}: $it") }
        }
    }
}

/** "All files access" granted, like the app's other permissions; Robolectric's app ops can't answer it. */
@Implements(android.os.Environment::class)
class AllFilesAccess : org.robolectric.shadows.ShadowEnvironment() {
    companion object {
        @JvmStatic @Implementation fun isExternalStorageManager(): Boolean = true
        @JvmStatic @Implementation fun isExternalStorageManager(file: File): Boolean = true
    }
}

/** Robolectric answers bindService with no service (a null ComponentName). Bind the app's own services for real. */
@Implements(className = "android.app.ContextImpl")
class BindsOwnServices : org.robolectric.shadows.ShadowContextImpl() {
    @Implementation
    override fun bindService(intent: Intent, conn: android.content.ServiceConnection, flags: Int): Boolean {
        connect(intent)
        return super.bindService(intent, conn, flags)
    }

    @Implementation
    override fun bindService(intent: Intent, flags: Int, executor: java.util.concurrent.Executor, conn: android.content.ServiceConnection): Boolean {
        connect(intent)
        return super.bindService(intent, flags, executor, conn)
    }

    private fun connect(intent: Intent) = runCatching {
        val app = org.robolectric.RuntimeEnvironment.getApplication()
        val component = intent.component ?: app.packageManager.resolveService(intent, 0)?.serviceInfo
            ?.let { android.content.ComponentName(it.packageName, it.name) } ?: return@runCatching
        if (component.packageName != app.packageName) return@runCatching
        val service = services.getOrPut(component.className) {
            @Suppress("UNCHECKED_CAST")
            val cls = Class.forName(component.className, true, app.classLoader) as Class<android.app.Service>
            Robolectric.buildService(cls, intent).create().get()
        }
        val binder = service.onBind(intent) ?: return@runCatching
        org.robolectric.Shadows.shadowOf(app).setComponentNameAndServiceForBindServiceForIntent(intent, component, binder)
    }.onFailure { System.err.println("noemu: binding ${intent.component ?: intent.action}: $it") }

    companion object {
        private val services = mutableMapOf<String, android.app.Service>()
    }
}

/** Robolectric's default network has no INTERNET capability, so apps show "offline" and skip sync. Report Wi-Fi. */
fun online(context: android.content.Context) = runCatching {
    val cm = context.getSystemService(android.net.ConnectivityManager::class.java)
    val caps = org.robolectric.shadows.ShadowNetworkCapabilities.newInstance()
    org.robolectric.Shadows.shadowOf(caps).apply {
        addTransportType(android.net.NetworkCapabilities.TRANSPORT_WIFI)
        addCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET)
        addCapability(android.net.NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        addCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    }
    org.robolectric.Shadows.shadowOf(cm).setNetworkCapabilities(cm.activeNetwork, caps)
}.onFailure { System.err.println("noemu: network: $it") }
