# indexer/apps/afterme/paper.py

import re
from web3 import Web3
from apps.afterme.abis import source_abi, will_abi
from apps.afterme.entities import Will
from datetime import datetime, timezone
import traceback

class Paper:
    def __init__(self, address, kind, web3, db, wills_collection_name):
        self.address = address
        self.kind = kind
        self.web3: Web3 = web3
        self.db = db
        self.wills_collection = db.collection(wills_collection_name)
        self.abi_string = None
        self.contract = None

        if kind == "afterme_source":
            self.abi_string = source_abi
        elif kind == "afterme_will":
            self.abi_string = will_abi
        
        if self.abi_string:
            self.abi = re.sub(r'\n+', ' ', self.abi_string).strip()
        else:
            self.abi = None

    def get_contract(self):
        if self.contract is None and self.address and self.abi:
            try:
                self.contract = self.web3.eth.contract(
                    address=Web3.to_checksum_address(self.address), abi=self.abi)
            except Exception as e:
                print(f"Error creating AfterMe contract object for {self.address} with kind {self.kind}: {e}")
                return None
        return self.contract

    def get_specific_contract(self, address, abi):
        """Helper to get a contract instance with a specific address and ABI."""
        try:
            final_abi = abi
            if isinstance(abi, str):
                final_abi = re.sub(r'\n+', ' ', abi).strip()
            return self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=final_abi)
        except Exception as e:
            print(f"Error creating specific AfterMe contract {address}: {e}")
            return None

    def get_onchain_will_data(self, will_address):
        """Fetches will details from the chain and formats it for Firestore."""
        will_contract = self.get_specific_contract(will_address, will_abi)
        if not will_contract:
            print(f"Could not instantiate Will contract at {will_address}")
            return None

        details = will_contract.functions.getWillDetails().call()
        (owner, interval, last_update, executed, has_diary, _eth_balance, 
         _heirs, _dist_percentages, _erc20s, _nfts) = details
        
        status = 'Active'
        if executed:
            status = 'Executed'
        elif interval == 0:
            status = 'Empty'

        will_entity = Will(
            address=will_address,
            owner=Web3.to_checksum_address(owner),
            interval=interval,
            last_update_timestamp=last_update,
            executed=executed,
            has_diary=has_diary,
            status=status
        )
        return will_entity.to_firestore()

    def handle_will_created(self, log):
        """Handles the WillCreated event from the source contract."""
        contract_instance = self.get_contract()
        if not contract_instance: return None

        try:
            decoded_event = contract_instance.events.WillCreated().process_log(log)
            will_address = Web3.to_checksum_address(decoded_event['args']['willAddress'])
            print(f"New Will created: {will_address}")

            will_data = self.get_onchain_will_data(will_address)
            if not will_data: return None

            self.wills_collection.document(will_address).set(will_data)
            print(f"Successfully stored new Will {will_address} in Firestore with status '{will_data.get('status')}'.")
            
            return will_address

        except Exception as e:
            print(f"Error processing WillCreated event: {e}")
            traceback.print_exc()
        return None

    def handle_will_configured(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Configuration received for Will: {will_address}")
        try:
            will_data = self.get_onchain_will_data(will_address)
            if not will_data: return
            self.wills_collection.document(will_address).update(will_data)
            print(f"Successfully updated and set Will {will_address} to 'Active'.")
        except Exception as e:
            print(f"Error handling WillConfigured for {will_address}: {e}")

    def handle_will_emptied(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Emptied event received for Will: {will_address}")
        try:
            will_data = self.get_onchain_will_data(will_address)
            if not will_data: return
            self.wills_collection.document(will_address).update(will_data)
            print(f"Successfully updated and set Will {will_address} to 'Empty'.")
        except Exception as e:
            print(f"Error handling WillEmptied for {will_address}: {e}")

    def handle_ping(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Ping received for Will: {will_address}")
        try:
            contract_instance = self.get_contract()
            if not contract_instance: return
            decoded_event = contract_instance.events.Ping().process_log(log)
            new_last_update_ts = decoded_event['args']['newLastUpdate']
            update_data = {
                'lastUpdate': datetime.fromtimestamp(new_last_update_ts, tz=timezone.utc),
                'lastIndexed': datetime.now(timezone.utc)
            }
            self.wills_collection.document(will_address).update(update_data)
            print(f"Successfully updated lastUpdate for Will {will_address}.")
        except Exception as e:
            print(f"Error handling Ping for Will {will_address}: {e}")

    def handle_executed(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        tx_hash = log['transactionHash'].hex()
        print(f"Execution reported for Will: {will_address} in Tx: {tx_hash}")
        try:
            update_data = {
                'executed': True,
                'status': 'Executed',
                'executionTime': datetime.now(timezone.utc),
                'transactionHash': tx_hash,
                'lastIndexed': datetime.now(timezone.utc)
            }
            self.wills_collection.document(will_address).update(update_data)
            print(f"Marked Will {will_address} as executed with status 'Executed'.")
        except Exception as e:
            print(f"Error marking Will {will_address} as executed: {e}")

    def handle_cancelled(self, log):
        """Handles the Cancelled event from a Will contract, deleting it and signaling removal."""
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Cancellation reported for Will: {will_address}")
        try:
            self.wills_collection.document(will_address).delete()
            print(f"Successfully deleted Will {will_address} from Firestore.")
            return "DELETED"  # Signal to the main loop to remove this from listeners
        except Exception as e:
            print(f"Error deleting Will {will_address}: {e}")
        return None
            
    def handle_event(self, log, func=None):
        if self.kind == "afterme_source":
            if func == "WillCreated":
                return self.handle_will_created(log)
        elif self.kind == "afterme_will":
            if func == "Ping":
                self.handle_ping(log)
            elif func == "Executed":
                self.handle_executed(log)
            elif func == "Cancelled":
                return self.handle_cancelled(log)
            elif func == "WillConfigured":
                self.handle_will_configured(log)
            elif func == "WillEmptied":
                self.handle_will_emptied(log)
        return None
# indexer/apps/afterme/paper.py