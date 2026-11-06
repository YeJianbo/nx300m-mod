use std::collections::HashMap;
use std::future::Future;
use std::net::Ipv4Addr;
use std::time::Duration;

use futures::{stream, StreamExt};
use mdns_sd::{ServiceDaemon, ServiceEvent};
use serde::{Deserialize, Serialize};
use tokio::net::TcpStream;
use tokio::sync::broadcast;
use tokio_util::sync::CancellationToken;
use ts_rs::TS;

use crate::utils::{is_not_self_ip, parse_mdns_endpoint_info};
use crate::DeviceType;

const PROBE_CONCURRENCY: usize = 4;
const ADDRESS_PROBE_TIMEOUT: Duration = Duration::from_millis(500);
const SERVICE_PROBE_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, PartialEq, Eq)]
enum ProbeOutcome {
    Reachable(Ipv4Addr),
    Exhausted,
    BudgetExpired,
    Cancelled,
}

fn peer_addresses(
    addresses: impl IntoIterator<Item = Ipv4Addr>,
    mut not_self: impl FnMut(&Ipv4Addr) -> bool,
) -> Vec<Ipv4Addr> {
    let mut addresses: Vec<_> = addresses
        .into_iter()
        .filter(|ip| {
            !ip.is_unspecified()
                && !ip.is_multicast()
                && !ip.is_broadcast()
                && !ip.is_loopback()
                && not_self(ip)
        })
        .collect();
    addresses.sort_unstable();
    addresses.dedup();
    addresses
}

fn prioritize_previous_address(
    addresses: &mut Vec<Ipv4Addr>,
    previous: Option<&EndpointInfo>,
    port: u16,
) -> bool {
    let advertised = previous
        .filter(|endpoint| {
            endpoint.port.as_deref().and_then(|p| p.parse::<u16>().ok()) == Some(port)
        })
        .and_then(|endpoint| endpoint.ip.as_deref()?.parse::<Ipv4Addr>().ok())
        .and_then(|ip| addresses.iter().position(|candidate| *candidate == ip));
    if let Some(position) = advertised {
        let ip = addresses.remove(position);
        addresses.insert(0, ip);
        true
    } else {
        false
    }
}

async fn probe_addresses<F, P>(
    addresses: Vec<Ipv4Addr>,
    ctk: &CancellationToken,
    address_timeout: Duration,
    total_timeout: Duration,
    probe: F,
) -> ProbeOutcome
where
    F: Fn(Ipv4Addr) -> P,
    P: Future<Output = bool>,
{
    // buffered polls a bounded set concurrently but yields in candidate order,
    // so connection timing cannot randomly change the chosen endpoint.
    let mut probes = Box::pin(
        stream::iter(addresses)
            .map(|ip| {
                let attempt = probe(ip);
                async move {
                    (
                        ip,
                        tokio::time::timeout(address_timeout, attempt)
                            .await
                            .unwrap_or(false),
                    )
                }
            })
            .buffered(PROBE_CONCURRENCY),
    );
    let first_reachable = async {
        while let Some((ip, reachable)) = probes.next().await {
            if reachable {
                return ProbeOutcome::Reachable(ip);
            }
        }
        ProbeOutcome::Exhausted
    };
    tokio::select! {
        biased;
        _ = ctk.cancelled() => ProbeOutcome::Cancelled,
        result = tokio::time::timeout(total_timeout, first_reachable) => result.unwrap_or(ProbeOutcome::BudgetExpired),
    }
    // Dropping the stream also drops every outstanding TCP connection future.
}

fn publish_probe_result(
    cache: &mut HashMap<String, EndpointInfo>,
    sender: &broadcast::Sender<EndpointInfo>,
    fullname: &str,
    result: ProbeOutcome,
    previous_advertised: bool,
    endpoint: impl FnOnce(Ipv4Addr) -> EndpointInfo,
) {
    match result {
        ProbeOutcome::Reachable(ip) => update_endpoint(cache, sender, fullname, Some(endpoint(ip))),
        ProbeOutcome::BudgetExpired if previous_advertised => {
            // The budget can expire before every address was attempted. An
            // unchanged advertised endpoint is not an offline observation.
        }
        ProbeOutcome::Cancelled => {}
        ProbeOutcome::BudgetExpired | ProbeOutcome::Exhausted => {
            update_endpoint(cache, sender, fullname, None);
        }
    }
}

fn update_endpoint(
    cache: &mut HashMap<String, EndpointInfo>,
    sender: &broadcast::Sender<EndpointInfo>,
    fullname: &str,
    replacement: Option<EndpointInfo>,
) {
    let old = cache.get(fullname);
    if old.map(|endpoint| &endpoint.id) != replacement.as_ref().map(|endpoint| &endpoint.id) {
        if let Some(mut removed) = cache.remove(fullname) {
            // The native panel needs ip/name/port to identify even a removal.
            // Preserve the previous endpoint fields instead of sending only id.
            removed.present = Some(false);
            let _ = sender.send(removed);
        }
    }
    if let Some(endpoint) = replacement {
        cache.insert(fullname.to_string(), endpoint.clone());
        let _ = sender.send(endpoint);
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, TS)]
#[ts(export)]
pub struct EndpointInfo {
    pub fullname: String,
    pub id: String,
    pub name: Option<String>,
    pub ip: Option<String>,
    pub port: Option<String>,
    pub rtype: Option<DeviceType>,
    pub present: Option<bool>,
}

pub struct MDnsDiscovery {
    daemon: ServiceDaemon,
    sender: broadcast::Sender<EndpointInfo>,
}

impl MDnsDiscovery {
    pub fn new(sender: broadcast::Sender<EndpointInfo>) -> Result<Self, anyhow::Error> {
        let daemon = ServiceDaemon::new()?;

        Ok(Self { daemon, sender })
    }

    pub async fn run(self, ctk: CancellationToken) -> Result<(), anyhow::Error> {
        info!("MDnsDiscovery: service starting");

        let service_type = "_FC9F5ED42C8A._tcp.local.";
        let receiver = self.daemon.browse(service_type)?;

        // Map with fullname as key and EndpointInfo as value
        let mut cache: HashMap<String, EndpointInfo> = HashMap::new();

        loop {
            tokio::select! {
                _ = ctk.cancelled() => {
                    info!("MDnsDiscovery: tracker cancelled, breaking");
                    break;
                }
                r = receiver.recv_async() => {
                    match r {
                        Ok(event) => {
                            match event {
                                ServiceEvent::ServiceResolved(info) => {
                                    let port = info.get_port();

                                    // Decode the "n" text properties
                                    let n = match info.get_property("n") {
                                        Some(_n) => _n,
                                        None => continue,
                                    };

                                    // Parse the endpoint info
                                    let (dt, dn) = match parse_mdns_endpoint_info(n.val_str()) {
                                        Ok(r) => r,
                                        Err(error) => {
                                            debug!("Ignoring invalid mDNS endpoint {}: {error}", info.get_fullname());
                                            continue;
                                        }
                                    };

                                    let dn = if dn.is_empty() {
                                        info.get_hostname().trim_end_matches('.').to_string()
                                    } else { dn };

                                    let fullname = info.get_fullname().to_string();
                                    let mut addresses = peer_addresses(info.get_addresses_v4().into_iter().copied(), is_not_self_ip);
                                    let previous_advertised = prioritize_previous_address(&mut addresses, cache.get(&fullname), port);
                                    let result = if port == 0 { ProbeOutcome::Exhausted } else {
                                        probe_addresses(addresses, &ctk, ADDRESS_PROBE_TIMEOUT, SERVICE_PROBE_TIMEOUT,
                                            |ip| async move { TcpStream::connect((ip, port)).await.is_ok() }).await
                                    };
                                    if result == ProbeOutcome::Cancelled { break; }
                                    publish_probe_result(&mut cache, &self.sender, &fullname, result, previous_advertised,
                                        |ip| {
                                            let endpoint = EndpointInfo {
                                                fullname: fullname.clone(),
                                                id: format!("{ip}:{port}"),
                                                name: Some(dn),
                                                ip: Some(ip.to_string()),
                                                port: Some(port.to_string()),
                                                rtype: Some(dt),
                                                present: Some(true),
                                            };
                                            info!("ServiceResolved: Resolved a new service: {:?}", endpoint);
                                            endpoint
                                        });
                                }
                                ServiceEvent::ServiceRemoved(_, fullname) => {
                                    trace!("ServiceRemoved: checking if should remove {}", fullname);
                                    update_endpoint(&mut cache, &self.sender, &fullname, None);
                                }
                                ServiceEvent::SearchStarted(_) | ServiceEvent::SearchStopped(_) => {}
                                _ => {}
                            }
                        },
                        Err(err) => error!("MDnsDiscovery: error: {}", err),
                    }
                }
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::future::pending;

    fn ip(last: u8) -> Ipv4Addr {
        Ipv4Addr::new(192, 0, 2, last)
    }

    #[test]
    fn filters_self_and_invalid_addresses_then_sorts() {
        let candidates = peer_addresses(
            [
                ip(9),
                ip(1),
                ip(4),
                ip(9),
                Ipv4Addr::UNSPECIFIED,
                Ipv4Addr::BROADCAST,
                Ipv4Addr::LOCALHOST,
                Ipv4Addr::new(224, 0, 0, 251),
            ],
            |candidate| *candidate != ip(1),
        );
        assert_eq!(candidates, vec![ip(4), ip(9)]);
    }

    #[tokio::test]
    async fn slow_address_does_not_hide_reachable_and_selection_is_stable() {
        let selected = probe_addresses(
            vec![ip(1), ip(2), ip(3)],
            &CancellationToken::new(),
            Duration::from_millis(20),
            Duration::from_secs(1),
            |address| async move {
                if address == ip(1) {
                    return pending::<bool>().await;
                }
                if address == ip(2) {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                true
            },
        )
        .await;
        // ip(3) finishes first, but stable ordering chooses reachable ip(2).
        assert_eq!(selected, ProbeOutcome::Reachable(ip(2)));
    }

    #[tokio::test]
    async fn pending_connections_stop_on_cancel_and_total_deadline() {
        let token = CancellationToken::new();
        let probe = probe_addresses(
            vec![ip(1)],
            &token,
            Duration::from_secs(10),
            Duration::from_secs(20),
            |_| pending::<bool>(),
        );
        let cancel = async {
            tokio::task::yield_now().await;
            token.cancel();
        };
        let (selected, ()) = tokio::time::timeout(Duration::from_secs(1), async {
            tokio::join!(probe, cancel)
        })
        .await
        .expect("cancellation must not wait for the TCP timeout");
        assert_eq!(selected, ProbeOutcome::Cancelled);

        let selected = tokio::time::timeout(
            Duration::from_secs(1),
            probe_addresses(
                vec![ip(1), ip(2)],
                &CancellationToken::new(),
                Duration::from_secs(10),
                Duration::from_millis(20),
                |_| pending::<bool>(),
            ),
        )
        .await
        .expect("the complete address search must have a deadline");
        assert_eq!(selected, ProbeOutcome::BudgetExpired);
        assert_eq!(
            probe_addresses(
                vec![ip(1)],
                &CancellationToken::new(),
                Duration::from_secs(1),
                Duration::from_secs(1),
                |_| async { false }
            )
            .await,
            ProbeOutcome::Exhausted
        );
    }

    #[test]
    fn replacement_and_removal_publish_old_endpoint_metadata_first() {
        let fullname = "receiver._FC9F5ED42C8A._tcp.local.";
        let endpoint = |address: Ipv4Addr| EndpointInfo {
            fullname: fullname.into(),
            id: format!("{address}:1234"),
            name: Some("Receiver".into()),
            ip: Some(address.to_string()),
            port: Some("1234".into()),
            present: Some(true),
            ..Default::default()
        };
        let (sender, mut events) = broadcast::channel(8);
        let mut cache = HashMap::new();
        update_endpoint(&mut cache, &sender, fullname, Some(endpoint(ip(1))));
        assert_eq!(events.try_recv().unwrap().present, Some(true));
        update_endpoint(&mut cache, &sender, fullname, Some(endpoint(ip(2))));
        let removed = events.try_recv().unwrap();
        assert_eq!(removed.id, "192.0.2.1:1234");
        assert_eq!(removed.present, Some(false));
        assert_eq!(removed.ip.as_deref(), Some("192.0.2.1"));
        assert_eq!(removed.name.as_deref(), Some("Receiver"));
        assert_eq!(removed.port.as_deref(), Some("1234"));
        assert_eq!(events.try_recv().unwrap().id, "192.0.2.2:1234");
        let mut advertised = vec![ip(1), ip(2), ip(3)];
        assert!(prioritize_previous_address(
            &mut advertised,
            cache.get(fullname),
            1234
        ));
        assert_eq!(advertised, vec![ip(2), ip(1), ip(3)]);
        publish_probe_result(
            &mut cache,
            &sender,
            fullname,
            ProbeOutcome::BudgetExpired,
            true,
            endpoint,
        );
        assert!(events.try_recv().is_err());
        assert_eq!(cache[fullname].id, "192.0.2.2:1234");
        assert!(!prioritize_previous_address(
            &mut advertised,
            cache.get(fullname),
            4321
        ));
        publish_probe_result(
            &mut cache,
            &sender,
            fullname,
            ProbeOutcome::BudgetExpired,
            false,
            endpoint,
        );
        let removed = events.try_recv().unwrap();
        assert_eq!(removed.id, "192.0.2.2:1234");
        assert_eq!(removed.present, Some(false));
        assert!(cache.is_empty());
    }
}
