    def _parse_token_event(self, data: dict) -> TokenEvent:
        # PumpPortal 'create' event provides 'solAmount' for the dev buy
        # Bonded curve/Existing tokens provide 'vSolInBondingCurve'
        sol_amount = data.get("solAmount")
        v_sol = data.get("vSolInBondingCurve")
        
        # Use solAmount if available (new creation), else fallback to vSolInBondingCurve (existing)
        liquidity = 0.0
        if sol_amount is not None:
            liquidity = float(sol_amount)
        elif v_sol is not None:
            liquidity = float(v_sol) / 1e9

        return TokenEvent(
            mint=data.get("mint"),
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "???"),
            creator=data.get("traderPublicKey") or data.get("creator"),
            market_cap_usd=float(data.get("marketCapSol", 0)) * self._telegram._sol_price,
            liquidity_sol=liquidity,
            timestamp=time(),
        )
