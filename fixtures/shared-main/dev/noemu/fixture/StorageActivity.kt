package dev.noemu.fixture

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import java.io.File

/** Shows what persisted storage holds, so tests can tell what one app run can see of another. */
class StorageActivity : Activity() {
    private val file get() = File(filesDir, "fixture-token")
    private val preferences get() = getSharedPreferences("fixture", MODE_PRIVATE)
    private val external get() = File("relative-token")

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val status = TextView(this)
        fun show() {
            status.text = "Stored: file=${file.takeIf { it.exists() }?.readText() ?: "none"} " +
                "prefs=${preferences.getString("token", "none")} " +
                "relative=${external.takeIf { it.exists() }?.readText() ?: "none"}"
        }
        show()
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(status)
            addView(Button(context).apply {
                text = "Store token"
                setOnClickListener {
                    file.writeText("token")
                    preferences.edit().putString("token", "token").commit()
                    external.writeText("token")
                    show()
                }
            })
        })
    }
}
