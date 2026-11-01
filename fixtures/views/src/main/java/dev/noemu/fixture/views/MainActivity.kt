package dev.noemu.fixture.views

import android.app.Activity
import android.os.Bundle
import android.view.View
import android.widget.*

/** Correct and deliberately broken state live on the same screen. No services or network required. */
class MainActivity : Activity() {
    private lateinit var good: EditText
    private lateinit var bad: EditText

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun button(label: String, action: () -> Unit = {}) = Button(this).apply {
            text = label
            setOnClickListener { action() }
            column.addView(this)
        }
        val status = TextView(this).apply { text = "ready" }
        column.addView(status)
        good = EditText(this).apply {
            id = View.generateViewId() // A valid View identity without a compiled resource name.
            hint = "Good input"; setText(state?.getString("good")); isSaveEnabled = false
        }
        bad = EditText(this).apply { id = R.id.broken_input; hint = "Broken input"; isSaveEnabled = false }
        column.addView(good)
        column.addView(bad)
        button("Disabled") { status.text = "BUG: disabled clicked" }.isEnabled = false
        button("Duplicate") { status.text = "BUG: first duplicate clicked" }
        button("Duplicate") { status.text = "BUG: second duplicate clicked" }
        button("Hidden") { status.text = "BUG: hidden clicked" }.visibility = View.GONE
        button("Crash") { error("fixture deliberate crash") }
        button("Change target") { status.text = "changed" }
        val scroller = HorizontalScrollView(this)
        val row = LinearLayout(this)
        for (i in 0..15) row.addView(TextView(this).apply { text = "Item $i"; minWidth = 300 })
        scroller.addView(row)
        column.addView(scroller)
        setContentView(column)
    }

    override fun onSaveInstanceState(out: Bundle) {
        out.putString("good", good.text.toString())
        // Deliberate fixture defect: bad.text is not saved.
        super.onSaveInstanceState(out)
    }
}
