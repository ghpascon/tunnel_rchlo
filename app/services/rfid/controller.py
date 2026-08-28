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

	# [BOX INFO]
	def update_box_info(self, box_info: str):
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
	def approve_box(self, name: str):
		asyncio.create_task(self._approve(name))
		self.state_sent = True

	def reject_box(self, name: str):
		asyncio.create_task(self._reject(name))
		self.state_sent = True

	async def _approve(self, name: str):
		self.last_tags = self.last_tags + [
			{'epc': tag.get('epc'), 'timestamp': datetime.now()} for tag in self.tags.get_all()
		]

		logging.info(f"{'='*20} Approving box {'='*20}")
		logging.info(f'Box info: {self.box_info}')
		success, msg = await self.devices.write_gpo(
			device_name=name, pin=1, state=True, control='pulsed', time=2000
		)
		if not success:
			error_msg = f'Failed to write GPO for approving box: {msg}'
			self.state_msg = {'text': error_msg, 'level': 'error'}
			logging.error(error_msg)
			# Save as rejected due to hardware/control error
			self.save_box_result(2)
		else:
			self.state_msg = {
				'text': f'Caixa {self.box_info.get("box_id")} aprovada com sucesso!',
				'level': 'success',
			}
			logging.info('GPO write successful for approving box')
			# Persist successful approval
			self.save_box_result(1)

	async def _reject(self, name: str):
		logging.info(f"{'='*20} Rejecting box {'='*20}")
		logging.info(f'Box info: {self.box_info}')
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
		self.save_box_result(2)

	def reset_box(self):
		self.box_info = {}
		# Allow processing of next box
		self.state_sent = False
		# NOTE: state_msg is intentionally NOT cleared here so the frontend
		# can still read the last result via /get_state before it is consumed.
		try:
			self.tags.clear()
		except Exception:
			# Defensive: TagList may not implement clear()
			pass

	# [VALIDATION]
	def _validate(self):
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
		state = self._validate()

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
				asyncio.create_task(
					delayed_function(
						self.validate_tags, settings.VALIDATION_TIME, name, make_action=True
					)
				)
		# Box NOK
		elif state == 2:
			# `_validate` may have already set a descriptive `state_msg` (e.g. unexpected SKU).
			if not self.state_msg:
				self.state_msg = {
					'text': 'Caixa rejeitada, quantidade excede o esperado',
					'level': 'error',
				}
			self.reject_box(name)

	def save_box_result(self, validation_state: int):
		state_str = None
		if validation_state == 1:
			state_str = 'approved'
		else:
			current_qty = len(self.tags)
			expected_qty = self.box_info.get('qty', 0)
			expected_sku = self.box_info.get('sku', None)
			if current_qty < expected_qty:
				state_str = 'rejected - not enough tags'
			elif current_qty > expected_qty:
				state_str = 'rejected - too many tags'
			current_skus = [tag.get('sku') for tag in self.tags.get_all()]
			for sku in current_skus:
				if sku != expected_sku:
					state_str = 'rejected - unexpected sku'

		if self.integration.db_manager is None:
			logging.warning('Database manager is not initialized. Skipping save_box_result.')
			return

		if not self.box_info or not self.box_info.get('box_id'):
			logging.warning('Box info is missing box_id. Skipping save_box_result.')
			return

		with self.integration.db_manager.get_session() as session:
			result = BoxResults(
				box_id=self.box_info.get('box_id', 'unknown'),
				sku=self.box_info.get('sku', 'unknown'),
				expected_qty=self.box_info.get('qty', 0),
				found_qty=len(self.tags),
				status=state_str,
			)
			logging.info(f'Box result: {result}')
			session.add(result)
			timestamp = datetime.now()
			for tag in self.tags.get_all():
				tag_in_box = TagsInBox(
					box_id=self.box_info.get('box_id', 'unknown'),
					timestamp=timestamp,
					epc=tag.get('epc'),
				)
				session.add(tag_in_box)
			session.commit()

	# Last Tags
	def epc_in_last_tags(self, epc: str) -> bool:
		return epc in [tag.get('epc') for tag in self.last_tags]

	def clear_old_last_tags(self, minutes: int = 5):
		cutoff_time = datetime.now() - timedelta(minutes=minutes)
		self.last_tags = [tag for tag in self.last_tags if tag.get('timestamp') > cutoff_time]
		logging.info(f'Cleared old last tags. Remaining tags count: {len(self.last_tags)}')
