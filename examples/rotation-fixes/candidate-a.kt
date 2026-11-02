package dev.noemu.fixture

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** Candidate A: restores what the screen showed. After rotation it looks right, but the count itself is lost. */
class CounterActivity : Activity() {
    private var count = 0
    private lateinit var label: TextView

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        label = TextView(this)
        fun show() {
            label.text = "Count: $count"
        }
        show()
        state?.getCharSequence("label")?.let { label.text = it }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(label)
            addView(Button(context).apply {
                text = "Add"
                setOnClickListener { count++; show() }
            })
        })
    }

    override fun onSaveInstanceState(out: Bundle) {
        super.onSaveInstanceState(out)
        out.putCharSequence("label", label.text)
    }
}
