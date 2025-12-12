# Workflow Integrity Audit — PaymentRequest

## Status inventory
الحالات المستخدمة حاليًا في سير العمل:
- `draft`
- `pending_pm`
- `pending_eng`
- `pending_finance`
- `ready_for_payment`
- `paid`
- `rejected`

(القيم القديمة `under_review_pm`/`under_review_eng`/`waiting_finance`/`approved` مذكورة فقط للتوافق في العرض ولا توجد عليها انتقالات جديدة.)

## Transition map (server-enforced)
| From | To | Permitted roles | Notes |
| --- | --- | --- | --- |
| draft | pending_pm | admin, engineering_manager, project_manager, engineer | إرسال المهندس/مدير المشروع للمراجعة |
| pending_pm | pending_eng | admin, engineering_manager, project_manager | موافقة مدير المشروع |
| pending_pm | rejected | admin, engineering_manager, project_manager | رفض مدير المشروع |
| pending_eng | pending_finance | admin, engineering_manager | موافقة الإدارة الهندسية |
| pending_eng | rejected | admin, engineering_manager | رفض الإدارة الهندسية |
| pending_finance | ready_for_payment | admin, finance | موافقة المالية الأولى |
| pending_finance | rejected | admin, finance | رفض المالية |
| ready_for_payment | paid | admin, finance | تسجيل الصرف الفعلي |

## Findings
- **التحقق كان موزعًا داخل كل Route**: كل Route كان يتحقق يدويًا من الحالة الحالية فقط، ما يجعل أي تغيير مستقبلي أو Route جديد عرضة لفقدان حارس الحالة أو تباين الأدوار المسموح بها.
- **عدم وجود حارس انتقالي موحد**: لم يكن هناك جدول مركزي يربط (الحالة الحالية → الحالة الهدف → الأدوار المصرح لها)، ما يزيد احتمالية السماح بانتقال غير مقصود عند تعديل الأكواد أو إخفاء زر في الواجهة فقط.

## Minimal fix المنفذ
- إضافة خريطة انتقالات مركزية مع حارس موحد (`_require_transition`) للتحقق من الدور والحالة الحالية قبل أي تغيير حالة.
- استبدال الشيكات المتفرقة في Routes باستخدام الحارس الموحد لرفض أي انتقال غير مصرح به برسالة واضحة.
