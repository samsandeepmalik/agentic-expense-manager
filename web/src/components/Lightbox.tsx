export function Lightbox({ txnId, onClose, onReupload }:
    { txnId: number; onClose: () => void; onReupload?: () => void }) {
  return (
    <div className="overlay deep" onClick={onClose}>
      <div className="lightbox-body" onClick={(e) => e.stopPropagation()}>
        <img src={`/api/receipts/${txnId}/preview`} alt="Receipt" className="lightbox-img" />
        <a className="ghost" href={`/api/receipts/${txnId}`} target="_blank" rel="noreferrer">
          Open original</a>
        {onReupload && (
          <button className="ghost" style={{ marginLeft: 8 }}
                  onClick={(e) => { e.stopPropagation(); onReupload(); }}>
            Re-upload to Drive</button>
        )}
      </div>
    </div>
  );
}
