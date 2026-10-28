package com.dfy.dfy_app

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

class MainActivity : FlutterActivity() {
    private val channel = "dfy/termux"

    // wait=true 的命令：sessionId -> 待回传的 MethodChannel result
    private val pending = ConcurrentHashMap<String, MethodChannel.Result>()
    private val timeouts = ConcurrentHashMap<String, Runnable>()
    private val reqCode = AtomicInteger(1)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var resultReceiver: BroadcastReceiver? = null

    companion object {
        const val ACTION_RESULT = "com.dfy.dfy_app.RUN_COMMAND_RESULT"
        const val EXTRA_SESSION_ID = "sessionId"
        const val TERMUX_PKG = "com.termux"
        const val TERMUX_PERM = "com.termux.permission.RUN_COMMAND"
        const val RESULT_BUNDLE = "result"
        const val RESULT_STDOUT = "stdout"
        const val RESULT_STDERR = "stderr"
        const val RESULT_EXIT_CODE = "exitCode"
        const val RESULT_ERRMSG = "errmsg"
        const val WAIT_TIMEOUT_MS = 30 * 60 * 1000L
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        resultReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                val sessionId = intent.getStringExtra(EXTRA_SESSION_ID)
                if (sessionId == null) return
                val result = pending.remove(sessionId) ?: return
                timeouts.remove(sessionId)?.let { mainHandler.removeCallbacks(it) }
                val bundle = intent.getBundleExtra(RESULT_BUNDLE)
                Log.i("DFY_TERMUX", "onReceive session=$sessionId hasResultBundle=${bundle != null}")
                val out = mutableMapOf<String, Any?>()
                if (bundle != null) {
                    out["exitCode"] = bundle.getInt(RESULT_EXIT_CODE, -1)
                    out["stdout"] = bundle.getString(RESULT_STDOUT) ?: ""
                    out["stderr"] = bundle.getString(RESULT_STDERR) ?: ""
                    out["errmsg"] = bundle.getString(RESULT_ERRMSG) ?: ""
                } else {
                    out["exitCode"] = -1
                    out["stdout"] = ""
                    out["stderr"] = ""
                    out["errmsg"] = "no result bundle"
                }
                Log.i("DFY_TERMUX", "onReceive session=$sessionId exitCode=${out["exitCode"]} errmsg=${out["errmsg"]}")
                result.success(out)
            }
        }
        val filter = IntentFilter(ACTION_RESULT)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(resultReceiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            registerReceiver(resultReceiver, filter)
        }
    }

    override fun onDestroy() {
        timeouts.values.forEach { mainHandler.removeCallbacks(it) }
        timeouts.clear()
        pending.values.forEach { runCatching { it.error("DESTROYED", "Activity destroyed", null) } }
        pending.clear()
        resultReceiver?.let { runCatching { unregisterReceiver(it) } }
        resultReceiver = null
        super.onDestroy()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "runCommand" -> {
                        val command = call.argument<String>("command")
                        val workdir = call.argument<String>("workdir")
                        val wait = call.argument<Boolean>("wait") ?: false
                        if (command.isNullOrBlank()) {
                            result.error("BAD_ARGS", "command required", null)
                        } else if (wait) {
                            val timeoutMs = (call.argument<Number>("timeoutMs"))?.toLong() ?: WAIT_TIMEOUT_MS
                            runAndWait(command, workdir, result, timeoutMs)
                        } else {
                            result.success(fire(command, workdir))
                        }
                    }
                    "preflight" -> result.success(preflight())
                    "requestPermission" -> {
                        requestPermission()
                        result.success(true)
                    }
                    "openAppSettings" -> {
                        openAppSettings()
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    // 预检：Termux 是否装、RUN_COMMAND 权限是否授。allow-external-apps 无法从这里查（受 ContentProvider 保护），交给向导文案引导。
    private fun preflight(): Map<String, Any> {
        val termuxInstalled = runCatching {
            packageManager.getPackageInfo(TERMUX_PKG, 0)
            true
        }.getOrDefault(false)
        val granted = checkSelfPermission(TERMUX_PERM) == PackageManager.PERMISSION_GRANTED
        return mapOf("termuxInstalled" to termuxInstalled, "runCommandGranted" to granted)
    }

    private fun requestPermission() {
        if (checkSelfPermission(TERMUX_PERM) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(TERMUX_PERM), 1)
        }
    }

    private fun openAppSettings() {
        val intent = Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:$packageName")
        )
        startActivity(intent)
    }

    // 发完即忘（用于长驻进程，如起 server.py）：返回 intent 是否成功发出。
    private fun fire(command: String, workdir: String?): Boolean {
        return try {
            startForegroundService(buildIntent(command, workdir, null))
            true
        } catch (e: Exception) {
            false
        }
    }

    // 等待命令结束并回传 stdout/stderr/exitCode（用于有限时长的安装命令）。
    private fun runAndWait(
        command: String,
        workdir: String?,
        result: MethodChannel.Result,
        timeoutMs: Long
    ) {
        val sessionId = UUID.randomUUID().toString()
        Log.i("DFY_TERMUX", "runAndWait session=$sessionId timeoutMs=$timeoutMs cmd=${command.take(60)}")
        pending[sessionId] = result
        val timeout = Runnable {
            timeouts.remove(sessionId)
            val r = pending.remove(sessionId)
            Log.w("DFY_TERMUX", "TIMEOUT session=$sessionId stillPending=${r != null}")
            r?.error("TIMEOUT", "命令超时（${timeoutMs / 1000} 秒）", null)
        }
        timeouts[sessionId] = timeout
        mainHandler.postDelayed(timeout, timeoutMs)
        try {
            startForegroundService(buildIntent(command, workdir, sessionId))
        } catch (e: Exception) {
            pending.remove(sessionId)
            timeouts.remove(sessionId)?.let { mainHandler.removeCallbacks(it) }
            Log.w("DFY_TERMUX", "START_FAILED session=$sessionId err=${e.message}")
            result.error("START_FAILED", e.message, null)
        }
    }

    private fun buildIntent(command: String, workdir: String?, sessionId: String?): Intent {
        return Intent("com.termux.RUN_COMMAND").apply {
            setPackage(TERMUX_PKG)
            putExtra("com.termux.RUN_COMMAND_PATH", "/system/bin/sh")
            putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arrayOf("-c", command))
            if (!workdir.isNullOrBlank()) {
                putExtra("com.termux.RUN_COMMAND_WORKDIR", workdir)
            }
            putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
            if (sessionId != null) {
                putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", sessionId)
                putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", resultPendingIntent(sessionId))
            }
        }
    }

    private fun resultPendingIntent(sessionId: String): PendingIntent {
        val intent = Intent(ACTION_RESULT).apply {
            setPackage(packageName)
            putExtra(EXTRA_SESSION_ID, sessionId)
        }
        return PendingIntent.getBroadcast(
            this,
            reqCode.getAndIncrement(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
        )
    }
}
