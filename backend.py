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

BASE_RPC = "https://base.llamarpc.com"  # Публичный RPC без лимитов
NFT_CONTRACT = os.getenv("NFT_CONTRACT", "0x...")  # Адрес твоего NFT контракта
ADMIN_PRIVATE_KEY = os.getenv("ADMIN_KEY")  # Приватный ключ для минта NFT
MINT_PRICE = int(os.getenv("MINT_PRICE", "1000000"))  # Цена в USDC (1000000 = 1 USDC)
RECIPIENT_ADDRESS = os.getenv("RECIPIENT_ADDRESS")  # Адрес получателя USDC (твой адрес)

w3 = Web3(Web3.HTTPProvider(BASE_RPC))

# Функция для логирования с flush
def log(message):
    print(message)
    sys.stdout.flush()

# ABI для NFT контракта (STUPID402NFT)
NFT_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "txHash", "type": "bytes32"}
        ],
        "name": "mintNFT",
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
    },
    {
        "inputs": [],
        "name": "totalSupply",
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

def verify_x402_payment(x_payment_header):
    """Проверяет x402 платеж и возвращает уникальный txHash"""
    log("🔍 Проверяем x402 платеж...")
    payment = decode_x402_payment(x_payment_header)
    
    if not payment['valid']:
        log("❌ Невалидный формат платежа")
        return False, "Invalid payment format", None
    
    log(f"✅ Платеж декодирован: from={payment['from']}, to={payment['to']}, value={payment['value']}")
    
    # Проверяем что to = адрес NFT контракта (USDC идут на контракт!)
    # Примечание: RECIPIENT_ADDRESS теперь = адрес NFT контракта
    if payment['to'].lower() != RECIPIENT_ADDRESS.lower():
        log(f"❌ Неправильный получатель: {payment['to']} != {RECIPIENT_ADDRESS}")
        return False, f"Wrong recipient: {payment['to']}", None
    
    if payment['value'] < MINT_PRICE:
        log(f"❌ Недостаточная сумма: {payment['value']} < {MINT_PRICE}")
        return False, f"Insufficient payment: {payment['value']} < {MINT_PRICE}", None
    
    # Создаем уникальный txHash из данных платежа
    # Используем keccak256(from + to + value + nonce)
    try:
        import hashlib
        
        # Создаем уникальный идентификатор из параметров платежа
        hash_data = f"{payment['from']}{payment['to']}{payment['value']}{payment['nonce']}".lower()
        tx_hash_bytes = Web3.keccak(text=hash_data)
        tx_hash = tx_hash_bytes.hex()
        
        log(f"🔐 Сгенерирован txHash: 0x{tx_hash}")
        log(f"✅ Платеж валиден! (x402 выполнит перевод USDC на контракт)")
        
        return True, "Payment valid", f"0x{tx_hash}"
        
    except Exception as e:
        log(f"❌ Ошибка генерации txHash: {str(e)}")
        return False, f"Error: {str(e)}", None

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
        response = jsonify({
            "error": "Payment required to access this resource",
            "x402Version": 1,
            "facilitator": "https://stupidx402.onrender.com/api/facilitate",
            "accepts": [{
                "scheme": "exact",
                "network": "base",
                "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "maxAmountRequired": str(MINT_PRICE),
                "payTo": RECIPIENT_ADDRESS,
                "resource": "https://stupidx402.onrender.com/api/mint",
                "description": "Mint 1 STUPID402 NFT.",
                "mimeType": "application/json",
                "maxTimeoutSeconds": 300,
                "outputSchema": {
                    "input": {
                        "type": "http",
                        "method": "GET",
                        "discoverable": True
                    },
                    "output": {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean"},
                            "tx": {"type": "string"},
                            "to": {"type": "string"},
                            "tokenId": {"type": "number"}
                        }
                    }
                },
                "extra": {
                    "recipientAddress": RECIPIENT_ADDRESS,
                    "name": "USD Coin",
                    "version": "2",
                    "primaryType": "TransferWithAuthorization",
                    "projectName": "STUPID402",
                    "projectDescription": "STUPID402 NFT - Mystery Box Collection. 1,000 max supply. Pure collectible on Base.",
                    "website": "https://stupidx402.onrender.com",
                    "icon": "https://stupidx402.onrender.com/static/icon.png"
                }
            }]
        })
        response.status_code = 402
        response.headers['Content-Type'] = 'application/json'
        return response
    
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
    
    # Проверяем x402 платеж и получаем txHash
    payment_valid, payment_message, tx_hash = verify_x402_payment(x_payment)
    if not payment_valid:
        log(f"❌ Платеж невалиден: {payment_message}")
        return jsonify({
            "x402Version": 1,
            "error": payment_message
        }), 402
    
    log(f"✅ Платеж валиден! txHash: {tx_hash}")
    log(f"ℹ️ USDC transfer должен быть выполнен facilitator или вручную")
    
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
        
        log(f"🎨 Минтим NFT с payment txHash: {tx_hash}...")
        # Строим транзакцию минта (mintNFT с txHash для защиты от double-spend)
        tx = nft_contract.functions.mintNFT(
            Web3.to_checksum_address(to_address),
            Web3.to_bytes(hexstr=tx_hash)
        ).build_transaction({
            'from': admin.address,
            'nonce': w3.eth.get_transaction_count(admin.address),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 8453  # Base chain ID
        })
        
        # Подписываем и отправляем
        signed = admin.sign_transaction(tx)
        mint_tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        log(f"🚀 NFT заминчен! TX: {mint_tx_hash.hex()}")
        
        response = jsonify({
            "x402Version": 1,
            "success": True,
            "tx": mint_tx_hash.hex(),
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
        total_supply = nft_contract.functions.totalSupply().call()
        max_supply = nft_contract.functions.MAX_SUPPLY().call()
    except Exception as e:
        log(f"⚠️ Error reading contract: {e}")
        total_supply = "unknown"
        max_supply = 1000
    
    return jsonify({
        "contract": NFT_CONTRACT,
        "price": MINT_PRICE,
        "price_usdc": MINT_PRICE / 1000000,
        "recipient": RECIPIENT_ADDRESS,
        "minted": total_supply,
        "maxSupply": max_supply
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok"})

@app.route('/api/facilitate', methods=['POST', 'OPTIONS'])
def facilitate():
    """
    Facilitator endpoint - выполняет USDC transfer используя подпись пользователя
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Получаем x-payment из body или headers
        x_payment = request.headers.get('x-payment') or request.json.get('payment')
        
        if not x_payment:
            return jsonify({"error": "Missing x-payment"}), 400
        
        log("🔧 Facilitator: начинаем USDC transfer...")
        payment_data = decode_x402_payment(x_payment)
        
        if not payment_data['valid']:
            return jsonify({"error": "Invalid payment"}), 400
        
        # USDC ABI для transferWithAuthorization
        usdc_abi = [
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
        
        # Парсим подпись
        signature = payment_data['signature']
        if signature.startswith('0x'):
            signature = signature[2:]
        
        sig_bytes = bytes.fromhex(signature)
        r = int.from_bytes(sig_bytes[:32], 'big')
        s = int.from_bytes(sig_bytes[32:64], 'big')
        v = sig_bytes[64]
        
        # USDC контракт
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
            abi=usdc_abi
        )
        
        admin = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
        
        # Вызываем transferWithAuthorization
        usdc_tx = usdc_contract.functions.transferWithAuthorization(
            Web3.to_checksum_address(payment_data['from']),
            Web3.to_checksum_address(payment_data['to']),
            int(payment_data['value']),
            int(payment_data['validAfter']),
            int(payment_data['validBefore']),
            Web3.to_bytes(hexstr=payment_data['nonce']),
            v,
            r,
            s
        ).build_transaction({
            'from': admin.address,
            'nonce': w3.eth.get_transaction_count(admin.address),
            'gas': 150000,
            'maxFeePerGas': w3.eth.gas_price * 2,
            'maxPriorityFeePerGas': w3.to_wei('0.001', 'gwei'),
            'chainId': 8453
        })
        
        signed_usdc = admin.sign_transaction(usdc_tx)
        usdc_tx_hash = w3.eth.send_raw_transaction(signed_usdc.raw_transaction)
        
        log(f"💸 Facilitator: USDC transfer TX: {usdc_tx_hash.hex()}")
        
        # Ждем подтверждения
        receipt = w3.eth.wait_for_transaction_receipt(usdc_tx_hash, timeout=30)
        
        if receipt.status == 1:
            log(f"✅ Facilitator: USDC transfer успешен!")
            return jsonify({
                "success": True,
                "tx": usdc_tx_hash.hex(),
                "from": payment_data['from'],
                "to": payment_data['to'],
                "value": payment_data['value']
            })
        else:
            log(f"❌ Facilitator: USDC transfer провалился")
            return jsonify({"error": "Transfer failed"}), 500
            
    except Exception as e:
        log(f"❌ Facilitator error: {str(e)}")
        log(f"📜 Traceback:\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

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

