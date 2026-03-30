"""
RFID models for SMARTX Connector.

Defines the Tag and Event models for storing RFID reader data
with proper indexing and relationships.
"""

try:
	from sqlalchemy import Column, Integer, String, Text, DateTime
except ImportError as e:
	raise ImportError(
		'SQLAlchemy is required. Please install it with: pip install sqlalchemy'
	) from e

from smartx_rfid.models import Base, BaseMixin


class Tag(Base, BaseMixin):
	"""
	RFID Tag model for storing tag read events.

	Stores information about RFID tags detected by readers,
	including EPC, TID, antenna, and signal strength data.
	"""

	__tablename__ = 'tags'

	# Primary key
	id = Column(Integer, primary_key=True, autoincrement=True)

	# Device identification
	device = Column(String(100), nullable=False)

	# RFID data fields
	epc = Column(String(24), nullable=False)

	tid = Column(String(24), nullable=True)

	ant = Column(Integer, nullable=True)
	rssi = Column(Integer, nullable=True)


class Event(Base, BaseMixin):
	"""
	RFID Event model for storing system and reader events.

	Stores various types of events from RFID readers and the system,
	including errors, status changes, and operational events.
	"""

	__tablename__ = 'events'

	# Primary key
	id = Column(Integer, primary_key=True, autoincrement=True)

	# Device identification
	device = Column(String(100), nullable=False)

	# Event classification
	event_type = Column(String(50), nullable=False)

	# Event data
	event_data = Column(Text, nullable=False)


class BoxResults(Base, BaseMixin):
	"""
	Model for storing results of box validations.

	Stores the results of validating boxes based on RFID tag reads,
	including the box information, validation status, and timestamps.
	"""

	__tablename__ = 'box_results'

	# Primary key
	id = Column(Integer, primary_key=True, autoincrement=True)

	# Box information
	box_id = Column(String(100), nullable=False)
	sku = Column(String(20), nullable=False)
	expected_qty = Column(Integer, nullable=False)
	found_qty = Column(Integer, nullable=False)

	status = Column(String(50), nullable=False)


class TagsInBox(Base, BaseMixin):
	"""
	Model for storing tags found in boxes.

	Stores the EPCs of tags found in boxes during validation,
	linked to the box results for reference.
	"""

	__tablename__ = 'tags_in_box'

	# Primary key
	id = Column(Integer, primary_key=True, autoincrement=True)
	# timestamp
	timestamp = Column(DateTime, nullable=False)

	# Box reference
	box_id = Column(String(100), nullable=False)

	# EPC of the tag found in the box
	epc = Column(String(24), nullable=False)
