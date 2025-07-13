# indexer/apps/afterme/paper.py

import re
from web3 import Web3
from apps.afterme.abis import source_abi, will_abi
from apps.afterme.entities import Will
from datetime import datetime, timezone

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

    def handle_will_created(self, log):
        """Handles the WillCreated event from the source contract."""
        contract_instance = self.get_contract()
        if not contract_instance: return None

        try:
            decoded_event = contract_instance.events.WillCreated().process_log(log)
            will_address = Web3.to_checksum_address(decoded_event['args']['willContract'])
            print(f"New Will created: {will_address}")

            will_contract = self.get_specific_contract(will_address, will_abi)
            if not will_contract:
                print(f"Could not instantiate new Will contract at {will_address}")
                return None

            # Call getWillDetails to fetch initial state
            # Response tuple: owner, interval, lastUpdate, executed, ...
            details = will_contract.functions.getWillDetails().call()
            owner, interval, last_update, executed = details[0], details[1], details[2], details[3]

            # Create a Will entity and save to Firestore
            will_entity = Will(
                address=will_address,
                owner=Web3.to_checksum_address(owner),
                interval=interval,
                last_update_timestamp=last_update,
                executed=executed
            )
            self.wills_collection.document(will_address).set(will_entity.to_firestore())
            print(f"Successfully stored new Will {will_address} in Firestore.")
            
            # Return the new address to be added to the listener
            return will_address

        except Exception as e:
            print(f"Error processing WillCreated event: {e}")
        return None

    def handle_ping(self, log):
        """Handles the Ping event from a Will contract."""
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Ping received for Will: {will_address}")
        will_contract = self.get_contract() # self.address is the will_address here
        if not will_contract: return

        try:
            # Re-fetch details to ensure consistency, as requested
            details = will_contract.functions.getWillDetails().call()
            new_last_update_ts = details[2]
            
            # Update the Firestore document
            update_data = {
                'lastUpdate': datetime.fromtimestamp(new_last_update_ts, tz=timezone.utc),
                'lastIndexed': datetime.now(timezone.utc)
            }
            self.wills_collection.document(will_address).update(update_data)
            print(f"Successfully updated lastUpdate for Will {will_address}.")

        except Exception as e:
            print(f"Error handling Ping for Will {will_address}: {e}")

    def handle_executed(self, log):
        """Handles the Executed event from a Will contract."""
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Execution reported for Will: {will_address}")
        try:
            update_data = {
                'executed': True,
                'lastIndexed': datetime.now(timezone.utc)
            }
            self.wills_collection.document(will_address).update(update_data)
            print(f"Marked Will {will_address} as executed.")
        except Exception as e:
            print(f"Error marking Will {will_address} as executed: {e}")

    def handle_cancelled(self, log):
        """Handles the Cancelled event from a Will contract, removing the will."""
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Cancellation reported for Will: {will_address}")
        try:
            self.wills_collection.document(will_address).delete()
            print(f"Successfully deleted Will {will_address} from Firestore.")
            # We don't need to return the address for removal from listening list
            # because the loop will naturally stop seeing events from it.
            # A more advanced indexer might remove it to save resources.
        except Exception as e:
            print(f"Error deleting Will {will_address}: {e}")
            
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
                self.handle_cancelled(log)
        return None