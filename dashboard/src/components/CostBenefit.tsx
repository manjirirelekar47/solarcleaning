import { inr } from "../format";
import type { Status } from "../types";

export default function CostBenefit({ status }: { status: Status }) {
  const value = status.net_benefit_inr;
  return (
    <div>
      <p className={`big ${value !== null && value < 0 ? "neg" : ""}`}>{inr(value)}</p>
      <p className="sub">net benefit of the latest decision (energy saved minus cleaning cost)</p>
      <p>{status.reason ?? "No reason recorded yet."}</p>
    </div>
  );
}
