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
import time

# Загружаем переменные из .env файла
load_dotenv()

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════
# НАСТРОЙКИ - ЗАПОЛНИ ИХ!
# ═══════════════════════════════════════════════════════════

BASE_RPC = "https://rpc.ankr.com/base/13ca64398a6a0125df8e188d1525542811320f016be33834bba2f4f32be7c4c8"  # Ankr RPC с API ключом
NFT_CONTRACT = os.getenv("NFT_CONTRACT", "0x...")  # Адрес твоего NFT контракта
ADMIN_PRIVATE_KEY = os.getenv("ADMIN_KEY")  # Приватный ключ для минта NFT
MINT_PRICE = int(os.getenv("MINT_PRICE", "1000000"))  # Цена в USDC (1000000 = 1 USDC)
RECIPIENT_ADDRESS = os.getenv("RECIPIENT_ADDRESS")  # Адрес получателя USDC (твой адрес)

w3 = Web3(Web3.HTTPProvider(BASE_RPC))

# Кэш для /api/info (чтобы не тормозить загрузку)
info_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 10  # Кэш на 10 секунд

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
        # Проверка на пустой x-payment
        if not x_payment_header:
            log(f"❌ x-payment пустой")
            return {'valid': False, 'error': 'Empty x-payment'}
        
        # Декодируем base64
        decoded = base64.b64decode(x_payment_header)
        payment_data = json.loads(decoded.decode('utf-8'))
        
        # Проверяем обязательные поля
        required_fields = ['from', 'to', 'value', 'nonce', 'validAfter', 'validBefore', 'signature']
        for field in required_fields:
            if not payment_data.get(field):
                log(f"❌ Отсутствует обязательное поле: {field}")
                return {'valid': False, 'error': f'Missing field: {field}'}
        
        log(f"✅ Платеж декодирован: from={payment_data.get('from')}, to={payment_data.get('to')}, value={payment_data.get('value')}")
        
        # Генерируем уникальный txHash для этой транзакции
        tx_hash = Web3.keccak(text=f"{payment_data.get('from')}{payment_data.get('nonce')}{payment_data.get('validBefore')}").hex()
        log(f"🔐 Сгенерирован txHash: {tx_hash}")
        
        return {
            'valid': True,
            'from': payment_data.get('from'),
            'to': payment_data.get('to'),
            'value': payment_data.get('value'),
            'nonce': payment_data.get('nonce'),
            'validAfter': payment_data.get('validAfter'),
            'validBefore': payment_data.get('validBefore'),
            'signature': payment_data.get('signature'),
            'txHash': tx_hash
        }
    except Exception as e:
        log(f"❌ Ошибка декодирования x-payment: {e}")
        return {'valid': False, 'error': str(e)}

# ═══════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/api/facilitate', methods=['POST', 'OPTIONS'])
def facilitate():
    """
    Facilitator endpoint - выполняет USDC transfer используя подпись пользователя
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        x_payment = request.headers.get('x-payment')
        if not x_payment and request.is_json:
            try:
                x_payment = request.json.get('payment')
            except:
                pass
        
        if not x_payment:
            return jsonify({"error": "Missing x-payment"}), 400
        
        log("🔧 Facilitator: начинаем USDC transfer...")
        
        payment_data = decode_x402_payment(x_payment)
        if not payment_data['valid']:
            return jsonify({"error": "Invalid payment"}), 400
        
        # ABI для USDC transferWithAuthorization
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
        signature = payment_data.get('signature')
        if not signature:
            log(f"❌ Отсутствует подпись в платеже")
            return jsonify({"error": "Missing signature"}), 400
        
        if signature.startswith('0x'):
            signature = signature[2:]
        
        sig_bytes = bytes.fromhex(signature)
        r = sig_bytes[:32]  # bytes32
        s = sig_bytes[32:64]  # bytes32
        v = sig_bytes[64]
        
        # Создаем контракт USDC
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
            abi=usdc_abi
        )
        
        # Получаем admin аккаунт
        admin = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
        
        # Создаем транзакцию
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
        
        # Подписываем и отправляем
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

@app.route('/api/mint', methods=['GET', 'POST', 'OPTIONS'])
def mint():
    """
    Endpoint для минта NFT
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Получаем x-payment из headers или body
        x_payment = request.headers.get('x-payment')
        if not x_payment and request.is_json:
            try:
                x_payment = request.json.get('payment')
            except:
                pass
        
        log(f"📝 Запрос минта для: {request.headers.get('x-forwarded-for', request.remote_addr)}")
        
        # Если нет x-payment - возвращаем 402
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
                        "projectDescription": "STUPID402 NFT Collection on Base",
                        "website": "https://stupidx402.onrender.com",
                        "icon": "https://stupidx402.onrender.com/static/icon.png"
                    }
                }]
            })
            response.status_code = 402
            response.headers['Content-Type'] = 'application/json'
            return response
        
        # Проверяем платеж
        log("🔍 Проверяем x402 платеж...")
        payment_data = decode_x402_payment(x_payment)
        
        if not payment_data['valid']:
            return jsonify({
                "x402Version": 1,
                "error": "Invalid payment"
            }), 400
        
        log(f"✅ Платеж валиден! (x402 выполнит перевод USDC на контракт)")
        log(f"✅ Платеж валиден! txHash: {payment_data['txHash']}")
        
        # ВЫПОЛНЯЕМ USDC TRANSFER СРАЗУ
        usdc_transfer_success = False
        try:
            log("💰 Вызываем facilitator для USDC transfer...")
            import requests as req
            facilitate_response = req.post(
                "https://stupidx402.onrender.com/api/facilitate",
                headers={"x-payment": x_payment},
                json={"payment": x_payment},
                timeout=60
            )
            
            if facilitate_response.status_code == 200:
                log(f"✅ USDC transfer выполнен! TX: {facilitate_response.json().get('tx')}")
                usdc_transfer_success = True
            else:
                log(f"❌ USDC transfer failed: {facilitate_response.text}")
        except Exception as e:
            log(f"⚠️ Ошибка facilitator: {str(e)}")
        
        if not usdc_transfer_success:
            log("❌ Останавливаем минт - USDC не списались")
            return jsonify({
                "x402Version": 1,
                "error": "USDC transfer failed"
            }), 500
        
        # Получаем текущий tokenId
        nft_contract = w3.eth.contract(
            address=Web3.to_checksum_address(NFT_CONTRACT),
            abi=NFT_ABI
        )
        
        current_token_id = nft_contract.functions.currentTokenId().call()
        log(f"📊 Текущий tokenId: {current_token_id}")
        
        # Минтим NFT
        user_address = Web3.to_checksum_address(payment_data['from'])
        tx_hash_bytes = Web3.to_bytes(hexstr=payment_data['txHash'])
        
        log(f"🎨 Минтим NFT с payment txHash: {payment_data['txHash']}...")
        
        admin_account = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)
        
        mint_tx = nft_contract.functions.mintNFT(
            user_address,
            tx_hash_bytes
        ).build_transaction({
            'from': admin_account.address,
            'nonce': w3.eth.get_transaction_count(admin_account.address),
            'gas': 200000,
            'maxFeePerGas': w3.eth.gas_price * 2,
            'maxPriorityFeePerGas': w3.to_wei('0.001', 'gwei'),
            'chainId': 8453
        })
        
        signed = admin_account.sign_transaction(mint_tx)
        mint_tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        log(f"⏳ Ждем подтверждения минта... TX: {mint_tx_hash.hex()}")
        
        receipt = w3.eth.wait_for_transaction_receipt(mint_tx_hash, timeout=60)
        
        if receipt.status == 1:
            log(f"✅ NFT #{current_token_id + 1} заминчен для {user_address}!")
            return jsonify({
                "success": True,
                "tx": mint_tx_hash.hex(),
                "to": user_address,
                "tokenId": current_token_id + 1,
                "x402Version": 1
            })
        else:
            log(f"❌ Минт провалился")
            return jsonify({
                "x402Version": 1,
                "error": "Mint transaction failed"
            }), 500
            
    except Exception as e:
        log(f"❌ Ошибка минта: {str(e)}")
        log(f"📜 Traceback:\n{traceback.format_exc()}")
        return jsonify({
            "x402Version": 1,
            "error": str(e)
        }), 500

@app.route('/api/info', methods=['GET'])
def info():
    """Информация о проекте (с кэшем)"""
    global info_cache
    
    # Проверяем кэш
    now = time.time()
    if info_cache["data"] and (now - info_cache["timestamp"]) < CACHE_TTL:
        return jsonify(info_cache["data"])
    
    # Обновляем кэш
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
    
    data = {
        "contract": NFT_CONTRACT,
        "price": MINT_PRICE,
        "price_usdc": MINT_PRICE / 1000000,
        "recipient": RECIPIENT_ADDRESS,
        "minted": total_supply,
        "maxSupply": max_supply
    }
    
    # Сохраняем в кэш
    info_cache["data"] = data
    info_cache["timestamp"] = now
    
    return jsonify(data)

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok"})

# CORS для всех endpoints
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,x-payment')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

if __name__ == '__main__':
    log("🚀 Запускаем NFT Mint API на порту 5000...")
    log(f"📝 NFT Contract: {NFT_CONTRACT}")
    log(f"💰 Mint Price: {MINT_PRICE / 1000000} USDC")
    log(f"📬 Recipient: {RECIPIENT_ADDRESS}")
    
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

