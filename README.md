The Crypto Slippage Analyzer is a web-based tool that estimates trade slippage, volatility, and execution risk for cryptocurrency orders. It uses real-time market data to simulate how order size and liquidity impact price movement.

The goal of this project is to help users understand potential trading risks before executing large orders.

Features:

	•	Real-time price and market data using CoinGecko API
	•	Slippage simulation based on order size and liquidity
	•	Volatility calculation using historical price data
	•	Risk scoring system (Low / Moderate / High)
	•	Slippage tolerance check with warning indicator
	•	Interactive charts for price history and slippage impact
	•	Input validation and default handling for stability
	•	Color-coded risk display for quick interpretation

The risk score is based on:

	•	Order size relative to market liquidity
	•	Asset volatility

Slippage tolerance is not included in the risk score but is used as a threshold warning to indicate whether a trade exceeds user expectations.
