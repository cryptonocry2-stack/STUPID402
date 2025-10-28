#!/usr/bin/env python3
"""
Backend API для NFT минта с x402
Полная рабочая версия для x402scan.com
"""

from flask import Flask, request, jsonify, Response, render_template
from web3 import Web3
from dotenv import load_dotenv
import os
import json
import base64
import sys
import traceback

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

# Функция для логирования с flush
def log(message):
    print(message)
    sys.stdout.flush()

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
    },
    {
        "inputs": [],
        "name": "MAX_SUPPLY",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ═══════════════════════════════════════════════════════════
# X402 ФУНКЦИИ
# ═══════════════════════════════════════════════════════════

# ABI для USDC контракта (EIP-3009)
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
        "name": "receiveWithAuthorization",
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
        log(f"❌ Ошибка декодирования x402: {e}")
        log(traceback.format_exc())
        return {'valid': False}

def execute_x402_payment(x_payment_header):
    """Выполняет receiveWithAuthorization для получения USDC"""
    log("🔍 Начинаем выполнение платежа...")
    payment = decode_x402_payment(x_payment_header)
    
    if not payment['valid']:
        log("❌ Невалидный формат платежа")
        return False, "Invalid payment format"
    
    log(f"✅ Платеж декодирован: from={payment['from']}, to={payment['to']}, value={payment['value']}")
    
    # Проверяем базовые параметры
    if payment['to'].lower() != RECIPIENT_ADDRESS.lower():
        log(f"❌ Неправильный получатель: {payment['to']} != {RECIPIENT_ADDRESS}")
        return False, f"Wrong recipient: {payment['to']}"
    
    if payment['value'] < MINT_PRICE:
        log(f"❌ Недостаточная сумма: {payment['value']} < {MINT_PRICE}")
        return False, f"Insufficient payment: {payment['value']} < {MINT_PRICE}"
    
    try:
        log("💰 Выполняем receiveWithAuthorization...")
        
        # Парсим подпись
        signature = payment['signature']
        if signature.startswith('0x'):
            signature = signature[2:]
        
        # EIP-2098 compact signature или standard
        if len(signature) == 130:  # Standard: r (32) + s (32) + v (1) = 65 bytes = 130 hex chars
            r = '0x' + signature[0:64]
            s = '0x' + signature[64:128]
            v = int(signature[128:130], 16)
        else:
            log(f"❌ Неправильная длина подписи: {len(signature)}")
            return False, f"Invalid signature length: {len(signature)}"
        
        # Нормализуем v (должно быть 27 или 28)
        if v < 27:
            v += 27
        
        log(f"🔐 Подпись: v={v}, r={r[:10]}..., s={s[:10]}...")
        
        # Создаем контракт USDC
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=USDC_ABI
        )
        
        # Получаем аккаунт админа (получателя)
        admin = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
        log(f"👤 Admin (получатель): {admin.address}")
        
        # Проверяем что admin.address == payment['to']
        if admin.address.lower() != payment['to'].lower():
            log(f"❌ Admin адрес не совпадает с получателем в платеже!")
            log(f"   Admin: {admin.address}, Payment to: {payment['to']}")
            return False, "Admin address mismatch"
        
        # Строим транзакцию receiveWithAuthorization
        # Получатель (to) ДОЛЖЕН вызывать эту функцию от своего имени
        tx = usdc_contract.functions.receiveWithAuthorization(
            Web3.to_checksum_address(payment['from']),      # from (отправитель USDC)
            Web3.to_checksum_address(payment['to']),        # to (получатель USDC, должен быть == admin)
            payment['value'],                                # value
            payment['validAfter'],                           # validAfter
            payment['validBefore'],                          # validBefore
            Web3.to_bytes(hexstr=payment['nonce']),         # nonce
            v,                                               # v
            Web3.to_bytes(hexstr=r),                        # r
            Web3.to_bytes(hexstr=s)                         # s
        ).build_transaction({
            'from': admin.address,
            'nonce': w3.eth.get_transaction_count(admin.address),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 8453
        })
        
        log("✍️ Подписываем и отправляем...")
        signed = admin.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        log(f"⏳ TX отправлена: {tx_hash.hex()}, ждем подтверждения...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        
        log(f"📋 Receipt: status={receipt['status']}, gasUsed={receipt['gasUsed']}")
        
        if receipt['status'] != 1:
            log(f"❌ Транзакция провалилась!")
            return False, "Payment transaction failed"
        
        log(f"💰 USDC получены! TX: {tx_hash.hex()}")
        return True, "Payment successful"
        
    except Exception as e:
        log(f"❌ Ошибка: {str(e)}")
        log(f"📜 Traceback:\n{traceback.format_exc()}")
        return False, f"Payment failed: {str(e)}"

# ═══════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route('/', methods=['GET'])
def index():
    """Главная страница с информацией о проекте"""
    return render_template('index.html', contract=NFT_CONTRACT)

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
    
    log(f"📝 Запрос минта для: {to_address}")
    
    # Выполняем receiveWithAuthorization для получения USDC
    payment_success, payment_message = execute_x402_payment(x_payment)
    if not payment_success:
        log(f"❌ Платеж не выполнен: {payment_message}")
        return jsonify({
            "x402Version": 1,
            "error": payment_message
        }), 402
    
    log(f"✅ USDC получены! Минтим NFT...")
    
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
            log(f"📊 Текущий tokenId: {current_token_id}")
        except:
            current_token_id = "unknown"
        
        log("🎨 Минтим NFT...")
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
        
        log(f"🚀 NFT заминчен! TX: {tx_hash.hex()}")
        
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
        log(f"❌ Ошибка минта: {str(e)}")
        log(f"📜 Traceback:\n{traceback.format_exc()}")
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
        max_supply = nft_contract.functions.MAX_SUPPLY().call()
    except Exception as e:
        log(f"⚠️ Error reading contract: {e}")
        current_token_id = "unknown"
        max_supply = 1000
    
    return jsonify({
        "contract": NFT_CONTRACT,
        "price": MINT_PRICE,
        "price_usdc": MINT_PRICE / 1000000,
        "recipient": RECIPIENT_ADDRESS,
        "minted": current_token_id,
        "maxSupply": max_supply
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
    log("═══════════════════════════════════════════════════════════")
    log("🚀 X402 NFT Mint API")
    log("═══════════════════════════════════════════════════════════")
    log(f"📍 Contract: {NFT_CONTRACT}")
    log(f"💰 Price: {MINT_PRICE / 1000000} USDC")
    log(f"📬 Recipient: {RECIPIENT_ADDRESS}")
    log("═══════════════════════════════════════════════════════════")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

