import { useEffect, useState } from "react";
import { getJson } from "../api";

interface GoogleStatus {
  configured: boolean;
  connected: boolean;
  ready: boolean;
  sheet_url: string | null;
}

interface WhatsAppQr {
  status: string;
  qr: string | null;
}

export function Connect() {
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [whatsapp, setWhatsapp] = useState<WhatsAppQr | null>(null);

  useEffect(() => {
    getJson<GoogleStatus>("/api/google/status").then(setGoogle).catch(() => {});
    const poll = () =>
      getJson<WhatsAppQr>("/api/whatsapp/qr").then(setWhatsapp).catch(() => {});
    poll();
    const interval = setInterval(poll, 4000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="connect-grid">
      <div className="panel">
        <h2>Google Drive & Sheets</h2>
        {!google ? (
          <p>Loading…</p>
        ) : !google.configured ? (
          <p className="error">
            Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env first.
          </p>
        ) : google.connected ? (
          <>
            <p>✅ Connected{google.ready ? " — spreadsheet ready" : ""}</p>
            {google.sheet_url && (
              <a href={google.sheet_url} target="_blank" rel="noreferrer">
                Open Google Sheet ↗
              </a>
            )}
          </>
        ) : (
          <a className="button" href="/api/google/auth">
            Connect Google
          </a>
        )}
      </div>

      <div className="panel">
        <h2>WhatsApp</h2>
        {!whatsapp ? (
          <p>Loading…</p>
        ) : whatsapp.status === "connected" ? (
          <p>✅ Connected — message your own number to chat with the agent.</p>
        ) : whatsapp.qr ? (
          <>
            <p>Scan with WhatsApp → Settings → Linked devices → Link a device</p>
            <img className="qr" src={whatsapp.qr} alt="WhatsApp QR code" />
          </>
        ) : whatsapp.status === "qr_expired" ? (
          <p className="error">QR expired — restart the backend to get a fresh code.</p>
        ) : (
          <p>Waiting for QR…</p>
        )}
      </div>
    </div>
  );
}
