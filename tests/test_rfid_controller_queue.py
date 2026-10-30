from app.services.rfid.controller import Controller


class FakeTags:
	def __init__(self):
		self.items = []
		self.clear_count = 0

	def clear(self):
		self.items.clear()
		self.clear_count += 1

	def get_all(self):
		return self.items

	def __len__(self):
		return len(self.items)


def make_controller():
	return Controller(devices=None, tags=FakeTags(), integration=None)


def test_non_aggregate_queue_does_not_replace_active_box_or_clear_tags():
	controller = make_controller()
	controller.update_box_info('BOX-1;2;123;2026-10-30')
	controller.tags.items.append({'epc': 'tag-read-for-box-1'})

	controller.update_box_info('BOX-2;3;456;2026-10-30')

	assert controller.box_info == {'box_id': 'BOX-1', 'qty': 2, 'sku': '00000000123'}
	assert controller.next_boxes == [
		{'box_id': 'BOX-1', 'qty': 2, 'sku': '00000000123'},
		{'box_id': 'BOX-2', 'qty': 3, 'sku': '00000000456'},
	]
	assert controller.tags.items == [{'epc': 'tag-read-for-box-1'}]


def test_duplicate_box_id_is_rejected_after_normalization():
	controller = make_controller()
	controller.update_box_info('BOX-1;2;123;2026-10-30')

	controller.update_box_info(' box-1 ;5;456;2026-10-30')

	assert len(controller.next_boxes) == 1
	assert controller.state_msg == {'text': 'Caixa já adicionada na fila', 'level': 'error'}


def test_aggregate_mode_combines_active_box_without_duplicate_queue_entries():
	controller = make_controller()
	controller.agregate_box = True
	controller.update_box_info('BOX-1;2;123;2026-10-30')
	controller.update_box_info('BOX-2;3;123;2026-10-30')

	assert controller.box_info == {
		'box_id': 'BOX-1|BOX-2',
		'qty': 5,
		'sku': '00000000123',
	}
	assert [box['box_id'] for box in controller.next_boxes] == ['BOX-1', 'BOX-2']


def test_approved_box_is_removed_and_next_box_becomes_active():
	controller = make_controller()
	controller.update_box_info('BOX-1;2;123;2026-10-30')
	controller.update_box_info('BOX-2;3;456;2026-10-30')

	controller._remove_approved_boxes({'box_id': 'BOX-1'})
	controller.reset_box()
	controller._activate_next_box()

	assert controller.next_boxes == [{'box_id': 'BOX-2', 'qty': 3, 'sku': '00000000456'}]
	assert controller.box_info == {'box_id': 'BOX-2', 'qty': 3, 'sku': '00000000456'}
