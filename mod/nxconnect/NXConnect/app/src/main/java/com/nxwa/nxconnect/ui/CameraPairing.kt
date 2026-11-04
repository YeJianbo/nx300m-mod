package com.nxwa.nxconnect.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.BroadcastReceiver
import android.content.IntentFilter
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.widget.EditText
import android.text.InputType
import androidx.activity.result.contract.ActivityResultContracts
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
    // Android 11+ saves the camera network through the system add-networks
    // dialog, making it a regular saved WLAN instead of an app-only request.
    private val systemSaveLauncher=activity.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()) { result ->
        // Per-network results are optional; SUCCESS and ALREADY_EXISTS both
        // leave a usable saved network behind.
        val perNetwork=result.data?.getIntegerArrayListExtra(
            android.provider.Settings.EXTRA_WIFI_NETWORK_RESULT_LIST)
        val ok=result.resultCode==Activity.RESULT_OK &&
            (perNetwork==null || perNetwork.all {
                it==android.provider.Settings.ADD_WIFI_RESULT_SUCCESS ||
                    it==android.provider.Settings.ADD_WIFI_RESULT_ALREADY_EXISTS })
        onSystemSaved(ok)
    }
    private var systemSaveAttempt=0
    private var systemSavePending=false
    private var systemSaveSsid: String?=null
    /** SSID the user added via the system dialog; survives until the Activity dies. */
    var boundSsid: String?=null
        private set
    val isWorking: Boolean get() = scanning || identification?.isActive == true || systemSavePending || CameraWifi.isPending(wifiOwner)
    fun dispose(cancelRequest: Boolean = true) {
        attempt++
        identification?.cancel();identification=null
        if (cancelRequest) CameraWifi.cancelPending(activity.applicationContext,wifiOwner)
        timeout?.let { handler.removeCallbacks(it) };timeout=null
        receiver?.let { runCatching { activity.unregisterReceiver(it) } };receiver=null;scanning=false
        systemSavePending=false
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
                        .setNegativeButton("取消") { _,_-> failed();status("已取消 WLAN 连接") }
                        .setOnCancelListener { failed();status("已取消 WLAN 连接") }.show()
                } else join(wifi.SSID,"",false)
            }.setNegativeButton("取消") { _,_-> failed();status("已取消 WLAN 选择") }
                .setOnCancelListener { failed();status("已取消 WLAN 选择") }.show()
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
        // The phone is already on this SSID (joined in system settings earlier):
        // identify directly instead of asking to save it again.
        if (isCurrentConnection(ssid)) { startIdentification(ssid,currentAttempt); return }
        if (Build.VERSION.SDK_INT >= 30 && joinViaSystemSave(ssid,password,wpa3,currentAttempt)) return
        joinViaSpecifier(ssid,password,wpa3,currentAttempt)
    }

    private fun isCurrentConnection(ssid:String): Boolean {
        val current=runCatching {
            (activity.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
                .connectionInfo?.ssid?.removeSurrounding("\"")
        }.getOrNull()
        return !current.isNullOrEmpty() && current!="<unknown ssid>" && current==ssid
    }

    /** Returns false when the system add-networks entry is unavailable, falling back to the specifier. */
    private fun joinViaSystemSave(ssid:String,password:String,wpa3:Boolean,currentAttempt:Int): Boolean {
        val suggestion=try {
            WifiNetworkSuggestion.Builder().setSsid(ssid).apply {
                if(password.isNotEmpty()) {
                    if(wpa3) setWpa3Passphrase(password) else setWpa2Passphrase(password)
                }
            }.build()
        } catch(e:IllegalArgumentException) { return false }
        val intent=Intent(android.provider.Settings.ACTION_WIFI_ADD_NETWORKS)
            .putParcelableArrayListExtra(android.provider.Settings.EXTRA_WIFI_NETWORK_LIST, arrayListOf(suggestion))
        systemSaveAttempt=currentAttempt
        systemSaveSsid=ssid
        systemSavePending=true
        status("请在系统弹窗允许添加并连接 $ssid")
        return try { systemSaveLauncher.launch(intent); true }
        catch(e:Exception) { systemSavePending=false; false }
    }

    private fun onSystemSaved(ok:Boolean) {
        systemSavePending=false
        if(systemSaveAttempt!=attempt||activity.isFinishing||activity.isDestroyed)return
        if(!ok) { failed();status("未添加相机网络，可重试或在系统 WLAN 设置中手动连接");return }
        startIdentification(systemSaveSsid ?: "",attempt)
    }

    /**
     * Saved-network identification: the camera's Network is whichever WLAN the
     * system connected, discovered per round instead of one captured request.
     */
    private fun startIdentification(ssid:String,currentAttempt:Int) {
        if(ssid.isNotEmpty()) boundSsid=ssid
        status("正在等待 $ssid 连接并识别相机…")
        identification=activity.lifecycleScope.launch {
            val match=try { withContext(Dispatchers.IO) {
                identifyCameraService(
                    isCurrent = { currentAttempt == attempt && !activity.isFinishing && !activity.isDestroyed },
                    candidates = { savedCandidates() },
                    probe = { ip ->
                        currentCoroutineContext().ensureActive()
                        probeAcrossNetworks(ip)
                    },
                )
            } } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                status("识别失败：${e.message}。请确认手机已连接 $ssid。")
                failed();return@launch
            }
            if(currentAttempt!=attempt||activity.isFinishing||activity.isDestroyed)return@launch
            if(match==null) {
                failed()
                status("暂未找到相机服务，请确认手机已连接 $ssid 后重试，或手动填写相机地址，端口 8781。")
                return@launch
            }
            val network=match.network ?: NetworkBinder.boundNetwork
            if(network==null) {
                failed()
                status("暂未找到相机服务，请确认手机已连接 $ssid 后重试，或手动填写相机地址，端口 8781。")
                return@launch
            }
            connected(match, network)
        }
    }

    /** Gateway, subnet and remembered addresses across every current WLAN/ethernet network. */
    private fun savedCandidates(): List<String> {
        val ctx=activity.applicationContext
        val cm=NetworkBinder.connectivity(ctx)
        val networks=NetworkBinder.candidateNetworks(ctx).filter { net ->
            runCatching { cm?.getNetworkCapabilities(net)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
                .getOrDefault(false)
        }
        val gateways=LinkedHashSet<String>()
        val locals=LinkedHashSet<String>()
        for(net in networks) {
            val props=NetworkBinder.linkProperties(ctx,net)
            props?.routes.orEmpty().mapNotNull { it.gateway?.hostAddress }
                .filter { it!="0.0.0.0"&&it.contains('.') }.forEach { gateways.add(it) }
            props?.linkAddresses.orEmpty().mapNotNull { (it.address as? java.net.Inet4Address)?.hostAddress }
                .forEach { locals.add(it) }
        }
        val prefs=activity.getSharedPreferences("nx",0)
        return (gateways + locals.map { it.substringBeforeLast('.')+".1" } +
            listOfNotNull(prefs.getString("last_ip",null)) +
            listOf("192.168.107.1","192.168.1.1","192.168.43.1"))
            .filter { it !in locals }
    }

    /** Verify one address on the network whose routes cover it first, then WLAN/ethernet order. */
    private suspend fun probeAcrossNetworks(ip:String): CameraDiscovery.Result.Found? {
        val ctx=activity.applicationContext
        val ordered=buildList {
            NetworkBinder.networkForAddress(ctx, ip)?.let(::add)
            addAll(NetworkBinder.candidateNetworks(ctx))
        }.distinct()
        for(net in ordered) {
            currentCoroutineContext().ensureActive()
            val caps=runCatching { NetworkBinder.connectivity(ctx)?.getNetworkCapabilities(net) }.getOrNull()
            if(caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true &&
                caps?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) != true) continue
            val found=CameraDiscovery.verify(ip, network=net) as? CameraDiscovery.Result.Found
            if(found!=null) return found
        }
        return null
    }

    private fun joinViaSpecifier(ssid:String,password:String,wpa3:Boolean,currentAttempt:Int) {
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
