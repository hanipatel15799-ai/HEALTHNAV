import asyncio
from answer_with_ai import answer_question

async def main():
    result = await answer_question(
        raw_question="What is low blood pressure?",
        patient_id="demo-patient-001",
        role="patient"
    )
    print(result)

asyncio.run(main())