#!/usr/bin/env python3
"""
Backend API для NFT минта с x402
Полная рабочая версия для x402scan.com
"""

from flask import Flask, request, jsonify, Response
from web3 import Web3
from dotenv import load_dotenv
import os
import json
import base64

# Загружаем переменные из .env файла
load_dotenv()

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════
# НАСТРОЙКИ - ЗАПОЛНИ ИХ!
# ═══════════════════════════════════════════════════════════

BASE_RPC = "https://rpc.ankr.com/base"
NFT_CONTRACT = os.getenv("NFT_CONTRACT", "0x...")  # Адрес твоего NFT контракта
ADMIN_PRIVATE_KEY = os.getenv("ADMIN_KEY")  # Приватный ключ для минта NFT
MINT_PRICE = int(os.getenv("MINT_PRICE", "1000000"))  # Цена в USDC (1000000 = 1 USDC)
RECIPIENT_ADDRESS = os.getenv("RECIPIENT_ADDRESS")  # Адрес получателя USDC (твой адрес)

w3 = Web3(Web3.HTTPProvider(BASE_RPC))

# ABI для NFT контракта
NFT_ABI = [
    {
        "inputs": [{"name": "to", "type": "address"}],
        "name": "mint",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "currentTokenId",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ═══════════════════════════════════════════════════════════
# X402 ФУНКЦИИ
# ═══════════════════════════════════════════════════════════

def decode_x402_payment(x_payment_header):
    """Декодирует x-payment header из x402"""
    try:
        # Декодируем base64
        decoded = base64.b64decode(x_payment_header)
        payment_data = json.loads(decoded)
        
        return {
            'from': payment_data['payload']['authorization']['from'],
            'to': payment_data['payload']['authorization']['to'],
            'value': int(payment_data['payload']['authorization']['value']),
            'valid': True
        }
    except Exception as e:
        print(f"Ошибка декодирования x402: {e}")
        return {'valid': False}

def verify_x402_payment(x_payment_header, expected_from):
    """Проверяет x402 платеж"""
    payment = decode_x402_payment(x_payment_header)
    
    if not payment['valid']:
        return False, "Invalid payment format"
    
    # Проверяем получателя (должен быть наш адрес)
    if payment['to'].lower() != RECIPIENT_ADDRESS.lower():
        return False, f"Wrong recipient: {payment['to']}"
    
    # Проверяем сумму (должна быть >= цены минта)
    if payment['value'] < MINT_PRICE:
        return False, f"Insufficient payment: {payment['value']} < {MINT_PRICE}"
    
    # Проверяем отправителя (должен совпадать с тем, кто минтит)
    if payment['from'].lower() != expected_from.lower():
        return False, f"Payment from {payment['from']} doesn't match minter {expected_from}"
    
    return True, "OK"

# ═══════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route('/api/mint', methods=['GET', 'POST', 'OPTIONS'])
def mint():
    """
    Минт NFT через x402
    
    Ожидается:
    - Header: x-payment (x402 payment token)
    - Query param или Body: to (адрес получателя NFT, опционально)
    """
    
    # OPTIONS для CORS
    if request.method == 'OPTIONS':
        return '', 204
    
    # Получаем адрес получателя NFT
    to_address = None
    if request.method == 'POST':
        try:
            # Пробуем получить JSON данные
            if request.is_json and request.json:
                to_address = request.json.get('to')
            # Если нет JSON, пробуем form data
            elif request.form:
                to_address = request.form.get('to')
        except:
            pass
    
    # Если не POST или нет данных, пробуем query params
    if not to_address:
        to_address = request.args.get('to')
    
    # Получаем x-payment header
    x_payment = request.headers.get('x-payment')
    
    # Если нет x-payment, возвращаем информацию о платеже (x402 accepts)
    if not x_payment:
        return jsonify({
            "x402Version": 1,
            "accepts": [{
                "scheme": "exact",
                "network": "base",
                "maxAmountRequired": str(MINT_PRICE),
                "resource": "https://stupid404.onrender.com/api/mint",
                "description": f"Mint STUPID402 NFT for {MINT_PRICE / 1000000} USDC",
                "mimeType": "application/json",
                "payTo": RECIPIENT_ADDRESS,
                "maxTimeoutSeconds": 300,
                "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC на Base
                "outputSchema": {
                    "input": {
                        "type": "http",
                        "method": "POST",
                        "bodyType": "json",
                        "bodyFields": {
                            "to": {
                                "type": "string",
                                "required": False,
                                "description": "NFT recipient address (optional, defaults to payer)"
                            }
                        }
                    },
                    "output": {
                        "success": {"type": "boolean"},
                        "tx": {"type": "string"},
                        "to": {"type": "string"},
                        "tokenId": {"type": "number"}
                    }
                }
            }]
        }), 402
    
    # Декодируем платеж, чтобы узнать отправителя
    payment = decode_x402_payment(x_payment)
    if not payment['valid']:
        return jsonify({
            "x402Version": 1,
            "error": "Invalid x-payment format"
        }), 402
    
    # Если адрес получателя не указан, используем отправителя платежа
    if not to_address:
        to_address = payment['from']
    
    print(f"📝 Запрос минта для: {to_address}")
    
    # Проверяем x402 платеж
    valid, message = verify_x402_payment(x_payment, to_address)
    if not valid:
        print(f"❌ Платеж невалиден: {message}")
        return jsonify({
            "x402Version": 1,
            "error": message
        }), 402
    
    print(f"✅ Платеж валиден!")
    
    # Минтим NFT
    try:
        if not ADMIN_PRIVATE_KEY:
            return jsonify({
                "x402Version": 1,
                "error": "ADMIN_KEY not configured"
            }), 500
        
        admin = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
        nft_contract = w3.eth.contract(
            address=Web3.to_checksum_address(NFT_CONTRACT), 
            abi=NFT_ABI
        )
        
        # Получаем текущий tokenId
        try:
            current_token_id = nft_contract.functions.currentTokenId().call()
            print(f"📊 Текущий tokenId: {current_token_id}")
        except:
            current_token_id = "unknown"
        
        # Строим транзакцию минта
        tx = nft_contract.functions.mint(
            Web3.to_checksum_address(to_address)
        ).build_transaction({
            'from': admin.address,
            'nonce': w3.eth.get_transaction_count(admin.address),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 8453  # Base chain ID
        })
        
        # Подписываем и отправляем
        signed = admin.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        
        print(f"🚀 NFT заминчен! TX: {tx_hash.hex()}")
        
        response = jsonify({
            "x402Version": 1,
            "success": True,
            "tx": tx_hash.hex(),
            "to": to_address,
            "tokenId": current_token_id + 1 if isinstance(current_token_id, int) else "check_on_chain"
        })
        
        # Добавляем специальный header для x402
        response.headers['X-PAYMENT-RESPONSE'] = 'accepted'
        
        return response
    
    except Exception as e:
        print(f"❌ Ошибка минта: {str(e)}")
        return jsonify({
            "x402Version": 1,
            "error": str(e)
        }), 500

@app.route('/api/info', methods=['GET'])
def info():
    """Информация о проекте"""
    try:
        nft_contract = w3.eth.contract(
            address=Web3.to_checksum_address(NFT_CONTRACT), 
            abi=NFT_ABI
        )
        current_token_id = nft_contract.functions.currentTokenId().call()
    except:
        current_token_id = "unknown"
    
    return jsonify({
        "contract": NFT_CONTRACT,
        "price": MINT_PRICE,
        "price_usdc": MINT_PRICE / 1000000,
        "recipient": RECIPIENT_ADDRESS,
        "minted": current_token_id
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok"})

# ═══════════════════════════════════════════════════════════
# CORS для x402scan.com
# ═══════════════════════════════════════════════════════════

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,x-payment')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.headers.add('Access-Control-Expose-Headers', 'X-PAYMENT-RESPONSE')
    return response

if __name__ == '__main__':
    print("═══════════════════════════════════════════════════════════")
    print("🚀 X402 NFT Mint API")
    print("═══════════════════════════════════════════════════════════")
    print(f"📍 Contract: {NFT_CONTRACT}")
    print(f"💰 Price: {MINT_PRICE / 1000000} USDC")
    print(f"📬 Recipient: {RECIPIENT_ADDRESS}")
    print("═══════════════════════════════════════════════════════════")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

