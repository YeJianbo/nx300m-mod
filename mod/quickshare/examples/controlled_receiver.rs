//! One-transfer test peer. Run only inside the probe's isolated network namespace.
//! The controller compares the independently derived sender/receiver PINs before
//! sending consent on stdin. This example is not installed in the camera bundle.
use std::{io::BufRead, path::PathBuf, time::Duration};

use anyhow::{bail, Context};
use rqs_lib::{
    channel::{ChannelAction, ChannelDirection, ChannelMessage},
    State, Visibility, RQS,
};
use serde_json::{json, Value};
use tokio::{sync::mpsc, time::timeout};

fn emit(value: Value) { println!("{value}"); }

async fn receive(peer: &mut RQS) -> anyhow::Result<()> {
    let mut events = peer.message_sender.subscribe();
    // Tokio's stdin uses an uncancellable blocking task, which would keep
    // runtime shutdown waiting for input after the transfer timeout.
    let (commands_tx, mut commands) = mpsc::channel(4);
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            if commands_tx.blocking_send(line).is_err() { break; }
        }
    });
    let mut consent: Option<(String, String)> = None;
    let mut accepted = false;
    let mut finished = false;
    loop {
        tokio::select! {
            command = commands.recv() => {
                let command = command.context("controller closed stdin before finish")??;
                let command: Value = serde_json::from_str(&command)?;
                match command.get("action").and_then(Value::as_str) {
                    Some("accept") => {
                        let (id, pin) = consent.as_ref().context("no pending consent")?;
                        if accepted || command.get("id").and_then(Value::as_str) != Some(id.as_str())
                            || command.get("pin").and_then(Value::as_str) != Some(pin.as_str()) {
                            bail!("consent does not match the pending transfer and PIN");
                        }
                        peer.message_sender.send(ChannelMessage {
                            id: id.clone(), direction: ChannelDirection::FrontToLib,
                            action: Some(ChannelAction::AcceptTransfer), ..Default::default()
                        })?;
                        accepted = true;
                        emit(json!({"event":"accepted","id":id}));
                    }
                    Some("finish") if accepted && finished => return Ok(()),
                    _ => bail!("unexpected controller command or premature finish"),
                }
            }
            event = events.recv() => {
                let event = event?;
                if event.direction != ChannelDirection::LibToFront { continue; }
                emit(json!({"event":"protocol","message":&event}));
                if let Some((id, _)) = &consent {
                    if event.id != *id { bail!("a second transfer is not allowed"); }
                }
                match event.state {
                    Some(State::WaitingForUserConsent) => {
                        let pin = event.meta.as_ref().and_then(|m| m.pin_code.clone())
                            .context("consent event has no authentication PIN")?;
                        if consent.is_some() { bail!("duplicate consent request"); }
                        consent = Some((event.id, pin));
                    }
                    Some(State::Finished) => {
                        if !accepted { bail!("transfer finished without controller consent"); }
                        finished = true;
                        emit(json!({"event":"receiver_finished","id":event.id}));
                        // The inbound core must stay alive to answer the sender's
                        // safe-disconnect request. The controller sends finish only
                        // after the sender itself reports success and exits zero.
                    }
                    Some(State::Disconnected | State::Rejected | State::Cancelled) =>
                        bail!("receiver failed: {:?}", event.state),
                    _ => {}
                }
            }
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .with_writer(std::io::stderr)
        .with_ansi(false)
        .init();
    let mut args = std::env::args().skip(1);
    let directory = PathBuf::from(args.next().context("usage: controlled_receiver EMPTY_DIRECTORY")?);
    if args.next().is_some() { bail!("unexpected argument"); }
    let directory = std::fs::canonicalize(directory)?;
    if std::fs::read_dir(&directory)?.next().is_some() { bail!("destination must be empty"); }
    let name = std::env::var("NX_QS_DEVICE_NAME").context("test receiver name must be set")?;
    let mut peer = RQS::new(Visibility::Invisible, None, Some(directory.clone()));
    peer.run().await?;
    peer.change_visibility(Visibility::Visible);
    emit(json!({"event":"ready","addr":peer.listening_addr,"name":name,"destination":directory}));
    let result = timeout(Duration::from_secs(180), receive(&mut peer)).await;
    // Stop on failures too; failure must never look like receiver completion.
    timeout(Duration::from_secs(5), peer.stop()).await.context("receiver shutdown timed out")?;
    result.context("controlled transfer timed out")??;
    emit(json!({"event":"receiver_stopped"}));
    Ok(())
}
