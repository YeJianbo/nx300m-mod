package com.nxwa.nxconnect.ui

import android.content.Context
import android.content.Intent
import android.content.BroadcastReceiver
import android.content.IntentFilter
import android.net.Network
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.widget.EditText
import android.text.InputType
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.nxwa.nxconnect.net.CameraWifi
import com.nxwa.nxconnect.net.CameraDiscovery
import com.nxwa.nxconnect.net.NetworkBinder
import com.nxwa.nxconnect.net.NfcWifiCredential
import com.nxwa.nxconnect.net.identifyCameraService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import com.nxwa.nxconnect.R

class CameraPairing(private val activity: AppCompatActivity,
    private val status: (String)->Unit, private val connected: (CameraDiscovery.Result.Found,Network)->Unit,
    private val failed: ()->Unit = {}) {
    private var receiver: BroadcastReceiver?=null
    private val handler=Handler(Looper.getMainLooper())
    private var timeout:Runnable?=null
    private var scanning=false
    private var attempt=0
    private var identification:Job?=null
    private val wifiOwner=Any()
    val isWorking: Boolean get() = scanning || identification?.isActive == true || CameraWifi.isPending(wifiOwner)
    fun dispose(cancelRequest: Boolean = true) {
        attempt++
        identification?.cancel();identification=null
        if (cancelRequest) CameraWifi.cancelPending(activity.applicationContext,wifiOwner)
        timeout?.let { handler.removeCallbacks(it) };timeout=null
        receiver?.let { runCatching { activity.unregisterReceiver(it) } };receiver=null;scanning=false
    }
    fun start() {
        if(scanning)return
        val manager=activity.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        if(!manager.isWifiEnabled) {
            NxDialogBuilder(activity).setTitle("开启 WLAN").setMessage("开启 WLAN 后即可查找相机。")
                .setPositiveButton("开启") { _,_-> activity.startActivity(Intent(android.provider.Settings.Panel.ACTION_WIFI)) }.setNegativeButton("取消",null).show()
            return
        }
        scanning=true;status("请开启相机无线连接，正在查找相机 WLAN…")
        fun results() {
            if(!scanning)return
            dispose()
            val networks=runCatching { manager.scanResults }.getOrElse { status("无法扫描 WLAN：${it.message}");return }
                .filter { it.SSID.isNotBlank() }.distinctBy { it.SSID }
                .sortedWith(compareByDescending<android.net.wifi.ScanResult> { it.SSID.contains("NX",true)||it.SSID.contains("SAMSUNG",true)||it.SSID.startsWith("AP_SSC") }.thenByDescending { it.level })
            if(networks.isEmpty()) { status("未发现 WLAN，请确认相机无线连接已开启，手机定位服务已打开后重试");return }
            val saved=activity.getSharedPreferences("camera_pair",0).getString("ssid",null)
            val remembered = networks.firstOrNull { it.SSID == saved && !it.capabilities.contains("WPA") && !it.capabilities.contains("SAE") }
            if (remembered != null) { join(remembered.SSID, "", false); return }
            val labels=networks.map { it.SSID + if(it.SSID==saved) " · 已绑定" else "" }.toTypedArray()
            NxDialogBuilder(activity).setTitle("选择相机 WLAN").setIcon(R.drawable.ic_wifi).setItems(labels) { _, index->
                val wifi=networks[index]
                val secured=wifi.capabilities.contains("WPA")||wifi.capabilities.contains("SAE")
                if(secured) {
                    val input=EditText(activity).apply { hint="相机显示的 WLAN 密码";inputType=InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD;setSingleLine() }
                    NxDialogBuilder(activity).setTitle(wifi.SSID).setIcon(R.drawable.ic_wifi).setView(input)
                        .setPositiveButton("连接") { _,_-> join(wifi.SSID,input.text.toString(),wifi.capabilities.contains("SAE")&&!wifi.capabilities.contains("WPA2")) }
                        .setNegativeButton("取消",null).show()
                } else join(wifi.SSID,"",false)
            }.setNegativeButton("取消",null).show()
        }
        val found=object:BroadcastReceiver() { override fun onReceive(ctx:Context?,intent:Intent?) { results() } }
        receiver=found
        ContextCompat.registerReceiver(activity,found,IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION),ContextCompat.RECEIVER_EXPORTED)
        timeout=Runnable { results() }.also { handler.postDelayed(it,6000) }
        try { if(!manager.startScan()) results() } catch(e:Exception) { dispose();status("扫描失败：${e.message}") }
    }
    fun joinCredential(credential: NfcWifiCredential) {
        dispose()
        join(credential.ssid, credential.password, credential.isWpa3)
    }
    private fun join(ssid:String,password:String,wpa3:Boolean) {
        dispose()
        val currentAttempt=attempt
        status("请在系统弹窗允许连接 $ssid")
        try { CameraWifi.join(activity.applicationContext,ssid,password,wpa3,wifiOwner) result@{ network,error->
            if(currentAttempt!=attempt||activity.isFinishing||activity.isDestroyed)return@result
            if(network==null) { failed();status(error ?: "连接失败");return@result }
            status("已加入 $ssid，正在识别相机…")
            identification=activity.lifecycleScope.launch {
                val match=try { withContext(Dispatchers.IO) {
                    val prefs=activity.getSharedPreferences("nx",0)
                    identifyCameraService(
                        isCurrent = { currentAttempt == attempt && CameraWifi.isCurrent(network) &&
                            !activity.isFinishing && !activity.isDestroyed },
                        candidates = {
                            val props=NetworkBinder.linkProperties(activity,network)
                            val gateways=props?.routes.orEmpty()
                                .mapNotNull { it.gateway?.hostAddress }.filter { it!="0.0.0.0"&&it.contains('.') }
                            val localAddresses=props?.linkAddresses.orEmpty().mapNotNull {
                                (it.address as? java.net.Inet4Address)?.hostAddress
                            }
                            val subnetHosts=localAddresses.map { it.substringBeforeLast('.')+".1" }
                            (gateways+subnetHosts+listOfNotNull(prefs.getString("last_ip",null))+
                                listOf("192.168.107.1","192.168.1.1","192.168.43.1"))
                                .filter { it !in localAddresses }
                        },
                        probe = { ip ->
                            currentCoroutineContext().ensureActive()
                            CameraDiscovery.verify(ip, network = network) as? CameraDiscovery.Result.Found
                        },
                    )
                } } catch (e: CancellationException) { throw e }
                catch (e: Exception) {
                    status("已保留 $ssid 的 WLAN 连接，识别失败：${e.message}")
                    failed();return@launch
                }
                if(currentAttempt!=attempt||!CameraWifi.isCurrent(network))return@launch
                if(match==null) {
                    failed()
                    status("已保留 $ssid 的 WLAN 连接，暂未找到相机服务。可点自动查找重试，或手动填写相机地址，端口 8781。")
                } else connected(match,network)
            }
        } } catch(e:Exception) { failed();status("无法连接：${e.message}") }
    }
}
