package dev.noemu.fixture.compose

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContent {
            MaterialTheme {
                var good by rememberSaveable { mutableStateOf("") }
                var bad by remember { mutableStateOf("") } // Deliberately lost on recreation.
                var status by remember { mutableStateOf("ready") }
                Column {
                    Text(status)
                    TextField(good, { good = it }, Modifier.testTag("good"), label = { Text("Good input") })
                    TextField(bad, { bad = it }, Modifier.testTag("bad"), label = { Text("Broken input") })
                    Button(onClick = { status = "BUG: disabled clicked" }, enabled = false) { Text("Disabled") }
                    Button(onClick = { status = "BUG: first duplicate clicked" }) { Text("Duplicate") }
                    Button(onClick = { status = "BUG: second duplicate clicked" }) { Text("Duplicate") }
                    Button(onClick = { error("fixture deliberate crash") }) { Text("Crash") }
                    Button(onClick = { status = "changed" }) { Text("Change target") }
                    Row(Modifier.testTag("horizontal").horizontalScroll(rememberScrollState())) {
                        repeat(16) { Text("Item $it", Modifier.width(100.dp)) }
                    }
                }
            }
        }
    }
}
