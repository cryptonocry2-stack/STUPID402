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

BASE_RPC = "https://mainnet.base.org"
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

# ABI для USDC контракта (EIP-3009 transferWithAuthorization)
USDC_ABI = [
    {
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "v", "type": "uint8"},
            {"name": "r", "type": "bytes32"},
            {"name": "s", "type": "bytes32"}
        ],
        "name": "transferWithAuthorization",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC на Base

def decode_x402_payment(x_payment_header):
    """Декодирует x-payment header из x402"""
    try:
        # Декодируем base64
        decoded = base64.b64decode(x_payment_header)
        payment_data = json.loads(decoded)
        
        auth = payment_data['payload']['authorization']
        signature = payment_data['payload']['signature']
        
        return {
            'from': auth['from'],
            'to': auth['to'],
            'value': int(auth['value']),
            'validAfter': int(auth['validAfter']),
            'validBefore': int(auth['validBefore']),
            'nonce': auth['nonce'],
            'signature': signature,
            'valid': True
        }
    except Exception as e:
        print(f"Ошибка декодирования x402: {e}")
        return {'valid': False}

def execute_x402_payment(x_payment_header):
    """Выполняет EIP-3009 transferWithAuthorization для получения USDC"""
    payment = decode_x402_payment(x_payment_header)
    
    if not payment['valid']:
        return False, "Invalid payment format", None
    
    # Проверяем базовые параметры
    if payment['to'].lower() != RECIPIENT_ADDRESS.lower():
        return False, f"Wrong recipient: {payment['to']}", None
    
    if payment['value'] < MINT_PRICE:
        return False, f"Insufficient payment: {payment['value']} < {MINT_PRICE}", None
    
    try:
        # Подготавливаем параметры для transferWithAuthorization
        signature = payment['signature']
        
        # Убираем '0x' и парсим подпись (r, s, v)
        if signature.startswith('0x'):
            signature = signature[2:]
        
        r = '0x' + signature[:64]
        s = '0x' + signature[64:128]
        v = int(signature[128:130], 16)
        
        # Создаем контракт USDC
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=USDC_ABI
        )
        
        # Получаем аккаунт админа
        admin = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
        
        # Строим транзакцию transferWithAuthorization
        tx = usdc_contract.functions.transferWithAuthorization(
            Web3.to_checksum_address(payment['from']),
            Web3.to_checksum_address(payment['to']),
            payment['value'],
            payment['validAfter'],
            payment['validBefore'],
            Web3.to_bytes(hexstr=payment['nonce']),
            v,
            Web3.to_bytes(hexstr=r),
            Web3.to_bytes(hexstr=s)
        ).build_transaction({
            'from': admin.address,
            'nonce': w3.eth.get_transaction_count(admin.address),
            'gas': 100000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 8453
        })
        
        # Подписываем и отправляем
        signed = admin.sign_transaction(tx)
        payment_tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        # Ждем подтверждения
        receipt = w3.eth.wait_for_transaction_receipt(payment_tx_hash, timeout=120)
        
        if receipt['status'] != 1:
            return False, "Payment transaction failed", None
        
        print(f"💰 USDC получены! TX: {payment_tx_hash.hex()}")
        return True, "Payment successful", payment_tx_hash.hex()
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Ошибка выполнения платежа: {error_msg}")
        return False, f"Payment execution failed: {error_msg}", None

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
                "resource": "https://stupid402.onrender.com/api/mint",
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
    
    # Выполняем x402 платеж (получаем USDC)
    print(f"💰 Выполняем transferWithAuthorization...")
    payment_success, payment_message, payment_tx = execute_x402_payment(x_payment)
    if not payment_success:
        print(f"❌ Платеж не выполнен: {payment_message}")
        return jsonify({
            "x402Version": 1,
            "error": payment_message
        }), 402
    
    print(f"✅ USDC получены! TX: {payment_tx}")
    
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
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        print(f"🚀 NFT заминчен! TX: {tx_hash.hex()}")
        
        response = jsonify({
            "x402Version": 1,
            "success": True,
            "mintTx": tx_hash.hex(),
            "paymentTx": payment_tx,
            "tx": tx_hash.hex(),  # для обратной совместимости
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

