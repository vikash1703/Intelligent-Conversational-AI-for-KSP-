import { useNavigate } from "react-router-dom";
import "./InfoTooltip.css";

// Shared ⓘ icon used across pages that used to carry a paragraph of
// data-limitation disclosure inline — that content now lives on one page
// (/dataset-notes). Every instance behaves the same: hover/focus shows a
// short tooltip, click navigates to Dataset Notes (or `to`, when a page
// wants to link a specific section instead of the page root).
export default function InfoTooltip({ text, to = "/dataset-notes" }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      className="info-tip"
      onClick={() => navigate(to)}
      title={text}
    >
      ⓘ
      <span className="tooltip-bubble" role="tooltip">{text}</span>
    </button>
  );
}
