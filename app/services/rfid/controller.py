import logging
from smartx_rfid.devices import DeviceManager
from smartx_rfid.utils import TagList
import asyncio
from smartx_rfid.utils import delayed_function
from app.core import settings
from datetime import datetime, timedelta
from app.models.rfid import BoxResults, TagsInBox
from .integration import Integration


class Controller:
	def __init__(self, devices: DeviceManager, tags: TagList, integration: Integration):
		self.box_info: dict = {}
		self.tags = tags
		self.devices = devices
		self.state_sent = False
		self.state_msg = {}
		self.integration = integration
		self.last_tags = []
		self._pending_validation_task = None

	def _cancel_pending_validation(self):
		task = self._pending_validation_task
		if task is not None and not task.done():
			task.cancel()
		self._pending_validation_task = None

	# [BOX INFO]
	def update_box_info(self, box_info: str):
		self._cancel_pending_validation()
		parts = box_info.replace('ç', ';').split(';')
		if len(parts) != 4:
			self.state_msg = {
				'text': 'Formato Inválido. Esperado: box_id;qtd;sku;datetime',
				'level': 'error',
			}
			logging.error(f'Invalid box info format: {box_info}')
			return
		box_id, qtd, sku, dt_str = parts
		if len(sku) <= 11:
			sku = sku.zfill(11)
		else:
			self.state_msg = {'text': 'SKU não pode ter mais de 11 caracteres', 'level': 'error'}
			logging.error(f'SKU cannot be longer than 11 characters: {sku}')
			return
		try:
			qtd = int(qtd)
		except (ValueError, TypeError):
			self.state_msg = {
				'text': 'Quantidade inválida nas informações da caixa',
				'level': 'error',
			}
			logging.error(f'Invalid quantity in box info: {qtd}')
			return

		logging.info(f'box_id={box_id}, qtd={qtd}, sku={sku}, datetime={dt_str}')

		self.box_info = {'box_id': box_id, 'qty': qtd, 'sku': sku}
		logging.info(f'Updating box info: {self.box_info}')
		self.state_msg = {'text': 'Informações da caixa atualizadas', 'level': 'success'}
		self.tags.clear()

	def validate_box_info(self, name: str):
		status = True
		if self.box_info.get('box_id') is None:
			msg = f'Box info is missing box_id for device {name}'
			logging.error(msg)
			self.state_msg = {'text': 'Informações da caixa indisponíveis', 'level': 'error'}
			status = False
		if self.box_info.get('qty', 0) <= 0:
			logging.warning('Box info has invalid quantity')
			self.state_msg = {
				'text': 'Quantidade inválida nas informações da caixa',
				'level': 'error',
			}
			status = False

		if not status:
			self.reject_box(name)
		return status

	# [ACTIONS]
	def approve_box(self, name: str, on_tolerance: bool = False):
		self._cancel_pending_validation()
		box_info_snapshot = self.box_info.copy()
		tags_snapshot = [dict(tag) for tag in self.tags.get_all()]
		asyncio.create_task(self._approve(name, box_info_snapshot, tags_snapshot, on_tolerance))
		self.state_sent = True

	def reject_box(self, name: str):
		self._cancel_pending_validation()
		box_info_snapshot = self.box_info.copy()
		tags_snapshot = [dict(tag) for tag in self.tags.get_all()]
		asyncio.create_task(self._reject(name, box_info_snapshot, tags_snapshot))
		self.state_sent = True

	async def _approve(
		self, name: str, box_info_snapshot: dict, tags_snapshot: list, on_tolerance: bool = False
	):
		self.last_tags = self.last_tags + [
			{'epc': tag.get('epc'), 'timestamp': datetime.now()} for tag in tags_snapshot
		]

		logging.info(f"{'='*20} Approving box {'='*20}")
		logging.info(f'Box info: {box_info_snapshot}')
		try:
			success, msg = await self.devices.write_gpo(
				device_name=name, pin=1, state=True, control='pulsed', time=2000
			)
			if not success:
				error_msg = f'Failed to write GPO for approving box: {msg}'
				self.state_msg = {'text': error_msg, 'level': 'error'}
				logging.error(error_msg)
				# Save as rejected due to hardware/control error
				self.save_box_result(2, box_info_snapshot, tags_snapshot)
			else:
				if on_tolerance:
					self.state_msg = {
						'text': f'Caixa {box_info_snapshot.get("box_id")} aprovada dentro da tolerância!',
						'level': 'info',
					}
				else:
					self.state_msg = {
						'text': f'Caixa {box_info_snapshot.get("box_id")} aprovada com sucesso!',
						'level': 'success',
					}
				logging.info('GPO write successful for approving box')
				# Persist successful approval
				self.save_box_result(1, box_info_snapshot, tags_snapshot)
		except Exception as e:
			logging.exception(f'Unexpected error while approving box: {e}')
			self.state_msg = {'text': 'Erro inesperado ao aprovar caixa', 'level': 'error'}
		finally:
			self.reset_box()

	async def _reject(self, name: str, box_info_snapshot: dict, tags_snapshot: list):
		logging.info(f"{'='*20} Rejecting box {'='*20}")
		logging.info(f'Box info: {box_info_snapshot}')
		try:
			success, msg = await self.devices.write_gpo(
				device_name=name, pin=2, state=True, control='pulsed', time=2000
			)
			if not success:
				error_msg = f'Failed to write GPO for rejecting box: {msg}'
				self.state_msg = {'text': error_msg, 'level': 'error'}
				logging.error(error_msg)
			else:
				logging.info('GPO write successful for rejecting box')
			# Save rejection result (2 = NOK)
			self.save_box_result(2, box_info_snapshot, tags_snapshot)
		except Exception as e:
			logging.exception(f'Unexpected error while rejecting box: {e}')
			self.state_msg = {'text': 'Erro inesperado ao reprovar caixa', 'level': 'error'}
		finally:
			self.reset_box()

	def reset_box(self):
		self._cancel_pending_validation()
		self.box_info = {}
		# NOTE: state_msg is intentionally NOT cleared here so the frontend
		# can still read the last result via /get_state before it is consumed.
		try:
			self.tags.clear()
		except Exception:
			# Defensive: TagList may not implement clear()
			pass

	# [VALIDATION]
	def _validate(self, make_action: bool = False):
		"""
		States:
		0 = Reading in progress
		1 = Box OK
		2 = Box NOK
		"""
		current_qty = len(self.tags)
		expected_qty = self.box_info.get('qty', 0)
		expected_sku = self.box_info.get('sku', None)

		if not expected_sku or expected_qty <= 0:
			self.state_msg = {'text': 'Informações da caixa indisponíveis', 'level': 'error'}
			return 2

		# Validate if has not unexpected skus
		current_skus = [tag.get('sku') for tag in self.tags.get_all()]
		for sku in current_skus:
			if sku != expected_sku:
				logging.warning(f'Unexpected SKU found: {sku}. Expected: {expected_sku}')
				self.state_msg = {
					'text': f'SKU inesperado encontrado: {sku}. Esperado: {expected_sku}',
					'level': 'error',
				}
				return 2

		# Validate quantity
		tolerance_qty = (settings.TOLERANCE_PERCENT / 100) * expected_qty
		if make_action:
			if current_qty < expected_qty and not current_qty + tolerance_qty < expected_qty:
				return 3
			elif current_qty > expected_qty and not current_qty - tolerance_qty > expected_qty:
				return 3

		if current_qty < expected_qty:
			return 0
		elif current_qty > expected_qty:
			return 2
		else:
			return 1

	def validate_tags(self, name: str, make_action: bool = False):
		if self.state_sent:
			return
		if not self.validate_box_info(name):
			return
		# Check if tag count matches box quantity
		if make_action:
			logging.info(f"{'='*20} Validating box {'='*20}")
		logging.info(f"Current qty: {len(self.tags)}, Expected qty: {self.box_info.get('qty', 0)}")
		state = self._validate(make_action)

		# Reading is still in progress, wait and re-validate
		if state == 0:
			if make_action:
				self.state_msg = {'text': 'Tags insuficientes', 'level': 'error'}
				self.reject_box(name)
		# Box OK
		elif state == 1:
			if make_action:
				self.approve_box(name)
			else:
				self._cancel_pending_validation()
				self._pending_validation_task = asyncio.create_task(
					delayed_function(
						self.validate_tags, settings.VALIDATION_TIME, name, make_action=True
					)
				)
		# Box NOK
		elif state == 2:
			self.reject_box(name)
		# Tolerance exceeded
		elif state == 3:
			if make_action:
				self.approve_box(name, on_tolerance=True)

	def save_box_result(
		self, validation_state: int, box_info_data: dict = None, tags_data: list = None
	):
		box_info_data = box_info_data if box_info_data is not None else self.box_info
		tags_data = tags_data if tags_data is not None else self.tags.get_all()

		state_str = None
		if validation_state == 1:
			state_str = 'approved'
		elif validation_state == 3:
			state_str = 'approved within tolerance'
		else:
			current_qty = len(tags_data)
			expected_qty = box_info_data.get('qty', 0)
			expected_sku = box_info_data.get('sku', None)
			if current_qty < expected_qty:
				state_str = 'rejected - not enough tags'
			elif current_qty > expected_qty:
				state_str = 'rejected - too many tags'
			current_skus = [tag.get('sku') for tag in tags_data]
			for sku in current_skus:
				if sku != expected_sku:
					state_str = 'rejected - unexpected sku'

		# Ensure we never attempt to insert a NULL `status` into the DB.
		if state_str is None:
			# Prefer the human-readable message if available.
			if isinstance(self.state_msg, dict) and self.state_msg.get('text'):
				state_str = f"rejected - {self.state_msg.get('text')}"
			else:
				state_str = 'rejected - unknown reason'

		logging.info(f'Final box status: {state_str}')

		if self.integration.db_manager is None:
			logging.warning('Database manager is not initialized. Skipping save_box_result.')
			return

		if not box_info_data or not box_info_data.get('box_id'):
			logging.warning('Box info is missing box_id. Skipping save_box_result.')
			return

		try:
			self.integration.db_manager.insert_record(
				BoxResults,
				{
					'box_id': box_info_data.get('box_id', 'unknown'),
					'sku': box_info_data.get('sku', 'unknown'),
					'expected_qty': box_info_data.get('qty', 0),
					'found_qty': len(tags_data),
					'status': state_str,
				},
			)
			tags_data = [
				{
					'box_id': box_info_data.get('box_id', 'unknown'),
					'timestamp': datetime.now(),
					'epc': tag.get('epc'),
				}
				for tag in tags_data
			]
			if tags_data:
				self.integration.db_manager.bulk_insert(
					TagsInBox,
					tags_data,
				)
		except Exception as e:
			logging.error(f'Failed to save box result: {e}')

	# Last Tags
	def epc_in_last_tags(self, epc: str) -> bool:
		return epc in [tag.get('epc') for tag in self.last_tags]

	def clear_old_last_tags(self, minutes: int = 5):
		cutoff_time = datetime.now() - timedelta(minutes=minutes)
		self.last_tags = [tag for tag in self.last_tags if tag.get('timestamp') > cutoff_time]
		logging.info(f'Cleared old last tags. Remaining tags count: {len(self.last_tags)}')
