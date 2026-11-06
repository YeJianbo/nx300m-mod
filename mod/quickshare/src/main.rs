use std::{path::PathBuf, time::Duration};

use anyhow::{anyhow, bail, Context};
use rqs_lib::{
    channel::{ChannelAction, ChannelDirection, ChannelMessage},
    OutboundPayload, SendInfo, State, Visibility, RQS,
};
use serde_json::json;
use tokio::{
    sync::broadcast,
    time::{timeout, Instant},
};

fn emit(value: serde_json::Value) {
    println!("{value}");
}

fn help() -> anyhow::Error {
    anyhow!("usage: nx-quickshare discover [seconds] | send IP:PORT NAME FILE... | loopback-test DIRECTORY")
}

struct CancellationSignal {
    #[cfg(unix)]
    interrupt: tokio::signal::unix::Signal,
    #[cfg(unix)]
    terminate: tokio::signal::unix::Signal,
    #[cfg(windows)]
    interrupt: tokio::signal::windows::CtrlC,
    #[cfg(windows)]
    terminate: tokio::signal::windows::CtrlBreak,
    #[cfg(windows)]
    close: tokio::signal::windows::CtrlClose,
}

impl CancellationSignal {
    fn new() -> std::io::Result<Self> {
        #[cfg(unix)]
        {
            use tokio::signal::unix::{signal, SignalKind};
            Ok(Self {
                interrupt: signal(SignalKind::interrupt())?,
                terminate: signal(SignalKind::terminate())?,
            })
        }
        #[cfg(windows)]
        {
            use tokio::signal::windows::{ctrl_break, ctrl_c, ctrl_close};
            Ok(Self {
                interrupt: ctrl_c()?,
                terminate: ctrl_break()?,
                close: ctrl_close()?,
            })
        }
    }

    #[cfg(unix)]
    async fn recv(&mut self) -> &'static str {
        tokio::select! {
            _ = self.interrupt.recv() => "interrupt",
            _ = self.terminate.recv() => "terminate",
        }
    }

    #[cfg(windows)]
    async fn recv(&mut self) -> &'static str {
        tokio::select! {
            _ = self.interrupt.recv() => "interrupt",
            _ = self.terminate.recv() => "terminate",
            _ = self.close.recv() => "console_close",
        }
    }
}

async fn cancel(
    rqs: &mut RQS,
    transfer: Option<(&str, &mut broadcast::Receiver<ChannelMessage>)>,
    signal: &str,
) -> anyhow::Result<()> {
    emit(json!({"event":"cancellation_requested", "signal":signal}));
    let mut local_cancel_observed = false;
    // Leave room for the camera service's SIGKILL fallback at two seconds.
    let shutdown_complete = timeout(Duration::from_millis(1500), async {
        if let Some((id, events)) = transfer {
            let _ = rqs.message_sender.send(ChannelMessage {
                id: id.into(),
                direction: ChannelDirection::FrontToLib,
                action: Some(ChannelAction::CancelTransfer),
                ..Default::default()
            });
            let _ = timeout(Duration::from_secs(1), async {
                loop {
                    match events.recv().await {
                        Ok(event) if event.direction == ChannelDirection::LibToFront
                            && event.id == id && event.state == Some(State::Cancelled) => {
                                local_cancel_observed = true;
                                break;
                            }
                        Ok(_) | Err(broadcast::error::RecvError::Lagged(_)) => {}
                        Err(broadcast::error::RecvError::Closed) => break,
                    }
                }
            }).await;
        }
        rqs.stop().await;
    }).await.is_ok();
    emit(json!({"event":"cancelled", "signal":signal,
        "local_cancel_observed":local_cancel_observed, "shutdown_complete":shutdown_complete}));
    bail!("operation cancelled ({signal})")
}

async fn sender() -> anyhow::Result<RQS> {
    let mut rqs = RQS::new(Visibility::Invisible, None, None);
    rqs.run_direct("127.0.0.1:0".parse()?).await?;
    Ok(rqs)
}

async fn discover(seconds: u64) -> anyhow::Result<()> {
    let mut cancellation = CancellationSignal::new()?;
    let mut rqs = sender().await?;
    let (tx, mut rx) = broadcast::channel(32);
    rqs.discovery(tx)?;
    let deadline = Instant::now() + Duration::from_secs(seconds);
    loop {
        tokio::select! {
            biased;
            signal = cancellation.recv() => return cancel(&mut rqs, None, signal).await,
            endpoint = timeout(deadline.saturating_duration_since(Instant::now()), rx.recv()) => {
                match endpoint {
                    Ok(Ok(endpoint)) => emit(json!({"event":"discovered", "endpoint":endpoint})),
                    _ => break,
                }
            }
        }
    }
    rqs.stop().await;
    Ok(())
}

async fn send(addr: String, name: String, files: Vec<String>) -> anyhow::Result<()> {
    let mut cancellation = CancellationSignal::new()?;
    let _: std::net::SocketAddr = addr
        .parse()
        .context("target must be an IP:PORT obtained from discovery")?;
    if files.is_empty() {
        bail!("at least one file is required");
    }
    for file in &files {
        if !std::fs::metadata(file)?.is_file() {
            bail!("not a regular file: {file}");
        }
    }
    let mut rqs = RQS::new(Visibility::Invisible, None, None);
    let mut events = rqs.message_sender.subscribe();
    let (tx, _) = rqs.run_direct("127.0.0.1:0".parse()?).await?;
    tx.send(SendInfo {
        id: addr.clone(),
        name,
        addr: addr.clone(),
        ob: OutboundPayload::Files(files),
    })
    .await?;
    let mut cancelled_signal = None;
    let result = async {
        let mut deadline = Instant::now() + Duration::from_secs(180);
        let mut previous_state = None;
        let mut previous_sent = 0;
        loop {
            let event = tokio::select! {
                biased;
                signal = cancellation.recv() => {
                    cancelled_signal = Some(signal);
                    bail!("operation cancelled ({signal})");
                }
                event = timeout(deadline.saturating_duration_since(Instant::now()), events.recv()) =>
                    event.context("no transfer progress or receiver confirmation for 180 seconds")??,
            };
            if event.direction != ChannelDirection::LibToFront {
                continue;
            }
            let sent = event.meta.as_ref().map(|m| m.sent_bytes).unwrap_or(0);
            // Large RAW files may take several minutes on the camera. Extend
            // the deadline only for protocol transitions or new file bytes.
            if event.state != previous_state || sent > previous_sent {
                deadline = Instant::now() + Duration::from_secs(180);
                previous_state = event.state.clone();
                previous_sent = sent;
            }
            emit(json!({"event":"transfer", "message":event}));
            match event.state {
                Some(State::Finished) => return Ok(()),
                Some(State::Disconnected | State::Rejected | State::Cancelled) => {
                    bail!("transfer ended: {:?}", event.state)
                }
                _ => {}
            }
        }
    }.await;
    if let Some(signal) = cancelled_signal {
        return cancel(&mut rqs, Some((&addr, &mut events)), signal).await;
    }
    rqs.stop().await;
    result
}

/// Exercise the actual upstream handshake and encrypted payload on TCP loopback.
/// Acceptance is allowed only after both independently derived PINs match.
async fn loopback(directory: PathBuf) -> anyhow::Result<()> {
    std::fs::create_dir_all(&directory)?;
    let directory = std::fs::canonicalize(directory)?;
    let source = directory.join("source");
    let destination = directory.join("received");
    std::fs::create_dir_all(&source)?;
    std::fs::create_dir_all(&destination)?;
    let input = source.join("nx-quickshare-fixture.bin");
    let output = destination.join("nx-quickshare-fixture.bin");
    if output.exists() {
        bail!(
            "test output already exists; use a fresh directory: {}",
            output.display()
        );
    }
    let fixture: Vec<u8> = (0..262_267_u32)
        .map(|i| ((i.wrapping_mul(37) ^ (i >> 8)) & 255) as u8)
        .collect();
    std::fs::write(&input, &fixture)?;

    let mut outgoing = RQS::new(Visibility::Invisible, None, None);
    let mut outgoing_events = outgoing.message_sender.subscribe();
    let (tx, _) = outgoing.run_direct("127.0.0.1:0".parse()?).await?;
    let mut incoming = RQS::new(Visibility::Invisible, None, Some(destination.clone()));
    let mut incoming_events = incoming.message_sender.subscribe();
    incoming.run_direct("127.0.0.1:0".parse()?).await?;
    let addr = incoming
        .listening_addr
        .context("listener address unavailable")?
        .to_string();
    tx.send(SendInfo {
        id: "loopback".into(),
        name: "Controlled loopback receiver".into(),
        addr,
        ob: OutboundPayload::Files(vec![input.to_string_lossy().into_owned()]),
    })
    .await?;

    let mut send_pin = None;
    let mut receive_pin = None;
    let mut consent_id = None;
    let mut accepted = false;
    let mut sender_finished = false;
    let mut receiver_finished = false;
    let result = timeout(Duration::from_secs(60), async {
        loop {
            let (side, event) = tokio::select! {
                e = outgoing_events.recv() => ("sender", e?),
                e = incoming_events.recv() => ("receiver", e?),
            };
            if event.direction != ChannelDirection::LibToFront { continue; }
            emit(json!({"event":"protocol", "side":side, "message":event}));
            let pin = event.meta.as_ref().and_then(|m| m.pin_code.clone());
            if side == "sender" { if pin.is_some() { send_pin = pin; } }
            else {
                if pin.is_some() { receive_pin = pin; }
                if event.state == Some(State::WaitingForUserConsent) { consent_id = Some(event.id.clone()); }
            }
            if !accepted && send_pin.is_some() && receive_pin.is_some() && consent_id.is_some() {
                if send_pin != receive_pin { bail!("UKEY2 authentication PIN mismatch"); }
                incoming.message_sender.send(ChannelMessage {
                    id:consent_id.clone().unwrap(), direction:ChannelDirection::FrontToLib,
                    action:Some(ChannelAction::AcceptTransfer), ..Default::default()
                })?;
                accepted = true;
                emit(json!({"event":"loopback_consent", "pins_equal":true}));
            }
            match event.state {
                Some(State::Finished) => {
                    if side == "sender" { sender_finished = true; } else { receiver_finished = true; }
                }
                Some(State::Disconnected | State::Rejected | State::Cancelled) => bail!("{side} failed: {:?}", event.state),
                _ => {}
            }
            if sender_finished && receiver_finished { break; }
        }
        // The upstream receive state is emitted after the final file write.
        let received = std::fs::read(&output)?;
        if received != fixture { bail!("received bytes differ from source"); }
        emit(json!({"event":"loopback_verified", "bytes_equal":true, "bytes":fixture.len(), "pins_equal":send_pin == receive_pin, "sender_finished":sender_finished, "receiver_finished":receiver_finished, "destination":output}));
        Ok::<(), anyhow::Error>(())
    }).await.context("loopback handshake/transfer timed out")?;
    incoming.stop().await;
    outgoing.stop().await;
    result
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(std::env::var("RUST_LOG").unwrap_or_else(|_| "error".into()))
        .with_writer(std::io::stderr)
        .init();
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("discover") => discover(args.next().unwrap_or_else(|| "15".into()).parse()?).await,
        Some("send") => {
            send(
                args.next().ok_or_else(help)?,
                args.next().ok_or_else(help)?,
                args.collect(),
            )
            .await
        }
        Some("loopback-test") => loopback(args.next().ok_or_else(help)?.into()).await,
        _ => Err(help()),
    }
}
