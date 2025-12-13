from typing import Dict, List, Any
from datetime import datetime
from src.portfolio.portfolio import PortfolioManager
from src.data.mongo import utc_now, jsonify

class ReportingEngine:
    """
    Generates human-readable and machine-structurable reports 
    from the PortfolioManager state.
    """
    def __init__(self, portfolio_manager: PortfolioManager):
        self.pm = portfolio_manager

    def generate_cycle_report(self, run_id: str, cycle_id: str) -> Dict[str, Any]:
        """
        Creates a full snapshot report for the current cycle.
        Ready to be stored in 'pnl_reports' collection.
        """
        summary = self.pm.get_portfolio_summary()
        
        report = {
            "run_id": run_id,
            "cycle_id": cycle_id,
            "timestamp": utc_now(),
            "firm_metrics": summary.get("firm_total", {}),
            "agent_metrics": {k: v for k, v in summary.items() if k != "firm_total"}
        }
        return jsonify(report)

    def get_manager_brief(self) -> str:
        """
        Generates a concise text summary for the Manager Agent's context.
        """
        summary = self.pm.get_portfolio_summary()
        firm = summary.get("firm_total", {})
        
        lines = [
            f"Firm Equity: ${firm.get('total_equity', 0):.2f} (ROI: {firm.get('roi_pct', 0):.2f}%)"
        ]
        
        agents = {k: v for k, v in summary.items() if k != "firm_total"}
        if not agents:
            lines.append("No active agent portfolios.")
        else:
            sorted_agents = sorted(agents.items(), key=lambda x: x[1]['roi_pct'], reverse=True)
            for agent_id, metrics in sorted_agents:
                lines.append(
                    f"- {agent_id}: Eq ${metrics['total_equity']:.2f}, "
                    f"ROI {metrics['roi_pct']:.2f}%, "
                    f"DD {metrics['max_drawdown_pct']:.2f}%, "
                    f"Sharpe {metrics['sharpe_ratio']:.2f}"
                )
                
        return "\n".join(lines)
