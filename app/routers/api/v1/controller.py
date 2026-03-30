from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from app.services import rfid_manager
from smartx_rfid.utils.path import get_prefix_from_path
from app.models.rfid import BoxResults, TagsInBox

router_prefix = get_prefix_from_path(__file__)
router = APIRouter(prefix=router_prefix, tags=[router_prefix])


@router.post('/inform_box')
async def inform_box(body: dict = Body(...)):
	box_info = body.get('box_info')
	rfid_manager.controller.update_box_info(box_info)
	return JSONResponse(content={'box_info': box_info})


@router.get('/box_info')
async def get_box_info():
	return JSONResponse(content=rfid_manager.controller.box_info)


@router.get('/get_state')
async def get_state():
	message = rfid_manager.controller.state_msg.copy()
	# Clear state message after sending
	rfid_manager.controller.state_msg = {}
	return JSONResponse(content=message)


@router.get(
	'/generate_results_report',
	summary='Generate table report',
	description='Generates a report for a specified database table.',
)
async def generate_results_report():
	try:
		results = rfid_manager.integration.generate_table_report(
			model=BoxResults, limit=1000000000, offset=0
		)
		rfid_manager.integration.db_manager.clear_table(
			BoxResults
		)  # Clear the table after generating the report
		return JSONResponse(content=results)
	except Exception as e:
		return JSONResponse(status_code=500, content={'error': str(e)})


@router.get(
	'/generate_tags_in_box_report',
	summary='Generate table report',
	description='Generates a report for a specified database table.',
)
async def generate_tags_in_box_report():
	try:
		results = rfid_manager.integration.generate_table_report(
			model=TagsInBox, limit=1000000000, offset=0
		)
		rfid_manager.integration.db_manager.clear_table(
			TagsInBox
		)  # Clear the table after generating the report
		return JSONResponse(content=results)
	except Exception as e:
		return JSONResponse(status_code=500, content={'error': str(e)})
