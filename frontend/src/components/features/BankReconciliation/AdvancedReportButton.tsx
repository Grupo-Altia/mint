import { FileSpreadsheet } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Button } from "@/components/ui/button"
import _ from "@/lib/translate"

const AdvancedReportButton = () => {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <Button 
                    variant={'outline'} 
                    size='icon' 
                    onClick={() => window.open('/app/query-report/Advanced Bank Reconciliation', '_blank')}
                >
                    <FileSpreadsheet />
                </Button>
            </TooltipTrigger>
            <TooltipContent>
                {_("Bank Reconciliation Report")}
            </TooltipContent>
        </Tooltip>
    )
}

export default AdvancedReportButton
